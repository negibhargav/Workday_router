"""
Workday Hire_Employee — SOAP service module.

Provides HireSOAPService, a plain Python class called directly by the
Executor pipeline. No MCP, no stdio server, no asyncio — just a clean
wrapper around the Workday Staffing SOAP Hire_Employee API.

DESIGN DIFFERENCE VS Get_Workers
----------------------------------
Get_Workers filters were all genuinely optional (pure narrowing).
Hire_Employee is a MUTATING business process — it creates a real employee
and kicks off an approval workflow. Fields are grouped into three tiers:

  TIER 1 - core (must be provided; call is rejected without them)
  TIER 2 - conditional (include only if mentioned: national ID, address,
            compensation, comments)
  TIER 3 - long tail (military service, disability status, hukou data,
            government IDs, etc.) — exposed as `advanced_fields` passthrough

SAFETY NOTE
-----------
This performs a REAL WRITE. Test against a Workday sandbox/preview tenant
until the mapping is verified. Keep auto_complete=False during testing so
the transaction lands in Workday's inbox for manual review.

CREDENTIALS
-----------
Set these three variables in your .env file:
    WORKDAY_WSDL_URL      — Staffing WSDL URL for your tenant
    WORKDAY_ISU_USERNAME  — ISU account (e.g. soap_user@tenant_name)
    WORKDAY_ISU_PASSWORD  — ISU account password

CHANGELOG (fixes applied)
--------------------------
1. ISU_PASSWORD now fails fast at import time instead of silently falling
   back to a placeholder string that would produce an opaque WSSE auth
   error deep inside a generic except-block.
2. Position_Details is only built (and only attached) when at least one
   Tier-2 position-detail field was actually supplied. Previously it was
   built unconditionally, which meant an empty <Position_Details/> block
   was sent even for hires that referenced an existing Position, and even
   when the caller supplied nothing at all. Whether Position_Details
   should be suppressed entirely when position_id is present depends on
   whether your tenant runs Position Management or Job Management — see
   the RESERVED_TIER1_KEYS / _POSITION_ID_SUPPRESSES_DETAILS toggle below.
3. advanced_fields (Tier 3 passthrough) can no longer silently clobber
   Tier 1 / Tier 2 fields that were already built. Any collision now
   raises a ValueError at request-build time instead of silently
   overwriting a validated reference with an unvalidated one.
4. SOAP faults are now caught specifically (zeep.exceptions.Fault) and
   their .detail is surfaced, instead of being flattened to str(exc) by
   a bare `except Exception`. Non-fault exceptions (network errors,
   factory/type errors, etc.) are still caught and re-raised with context,
   but are no longer indistinguishable from a Workday-side rejection.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from lxml import etree
from zeep import Client, Settings
from zeep.exceptions import Fault
from zeep.helpers import serialize_object
from zeep.transports import Transport
from zeep.wsse.username import UsernameToken

_hire_root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
load_dotenv(os.path.join(_hire_root, ".env"))

# ---------------------------------------------------------------------------
# 1. CONFIG — reads from .env; falls back to derived values only for the
#    WSDL URL / username (safe to guess a tenant path). The password is
#    NEVER guessed or defaulted — see fix #1.
# ---------------------------------------------------------------------------
raw_wsdl_url = os.getenv("WORKDAY_WSDL_URL")
base_url = os.getenv("WORKDAY_BASE_URL")

if not raw_wsdl_url or "/acme/" in raw_wsdl_url:
    if base_url:
        parsed = urlparse(base_url)
        host = parsed.netloc
        path_parts = [p for p in parsed.path.split("/") if p]
        if path_parts:
            tenant = path_parts[-1]
            WSDL_URL = f"https://{host}/ccx/service/{tenant}/Staffing/v43.0?wsdl"
            print(f"[HireSOAPService] Dynamically resolved WSDL_URL: {WSDL_URL}", file=sys.stderr)
        else:
            WSDL_URL = "https://wcpdev-services1.wd101.myworkday.com/ccx/service/jll_wcpdev1/Staffing/v43.0?wsdl"
    else:
        WSDL_URL = "https://wcpdev-services1.wd101.myworkday.com/ccx/service/jll_wcpdev1/Staffing/v43.0?wsdl"
else:
    WSDL_URL = raw_wsdl_url

raw_isu_username = os.getenv("WORKDAY_ISU_USERNAME")
if not raw_isu_username or "@acme" in raw_isu_username:
    if base_url:
        parsed = urlparse(base_url)
        path_parts = [p for p in parsed.path.split("/") if p]
        if path_parts:
            tenant = path_parts[-1]
            ISU_USERNAME = f"integration_user1@{tenant}"
        else:
            ISU_USERNAME = "integration_user1@jll_wcpdev1"
    else:
        ISU_USERNAME = "integration_user1@jll_wcpdev1"
else:
    ISU_USERNAME = raw_isu_username

# --- FIX #1: fail fast instead of silently defaulting to a placeholder ---
ISU_PASSWORD = os.getenv("WORKDAY_ISU_PASSWORD")
if not ISU_PASSWORD:
    raise RuntimeError(
        "[HireSOAPService] WORKDAY_ISU_PASSWORD is not set. Refusing to build "
        "a SOAP client with a placeholder credential — this used to fail "
        "silently deep inside a WSSE auth error. Set it in your .env file."
    )

# Toggle: does supplying an existing position_id mean Position_Details
# should be omitted entirely (pure Position Management), or can both be
# sent together (some Job Management configs)? Confirm against your tenant
# and flip if needed — see fix #2 changelog note above.
_POSITION_ID_SUPPRESSES_DETAILS = False

# ---------------------------------------------------------------------------
# 2. ZEEP CLIENT SETUP — built once, reused across calls (singleton)
# ---------------------------------------------------------------------------
_zeep_client: Client | None = None


def get_zeep_client() -> Client:
    global _zeep_client
    if _zeep_client is None:
        session = requests.Session()
        transport = Transport(session=session, timeout=30)
        settings = Settings(xml_huge_tree=True)
        _zeep_client = Client(
            wsdl=WSDL_URL,
            wsse=UsernameToken(ISU_USERNAME, ISU_PASSWORD),
            transport=transport,
            settings=settings,
        )
    return _zeep_client


# ---------------------------------------------------------------------------
# 3. HELPER — builds a simple <X_Reference><ID type="...">value</ID></X_Reference>
# ---------------------------------------------------------------------------
def _ref(factory, obj_type_name, id_type_name, value, id_type):
    obj_type = getattr(factory, obj_type_name)
    id_type_cls = getattr(factory, id_type_name)
    return obj_type(ID=[id_type_cls(_value_1=value, type=id_type)])


# ---------------------------------------------------------------------------
# 4. DYNAMIC REQUEST BUILDER
#    Only sets fields that were actually provided in `args`.
# ---------------------------------------------------------------------------
def build_hire_employee_request(client: Client, args: dict[str, Any]) -> dict:
    factory = client.type_factory("urn:com.workday/bsvc")

    # ---- Business_Process_Parameters ----
    bpp_fields = {}
    if "auto_complete" in args:
        bpp_fields["Auto_Complete"] = args["auto_complete"]
    if "run_now" in args:
        bpp_fields["Run_Now"] = args["run_now"]
    if args.get("comment"):
        bpp_fields["Comment_Data"] = factory.Business_Process_Comment_DataType(Comment=args["comment"])
    business_process_parameters = (
        factory.Business_Process_ParametersType(**bpp_fields) if bpp_fields else None
    )

    # ---- Person identification (choice) ----
    hire_data_fields: dict[str, Any] = {}
    worker_type = args.get("existing_worker_type")
    worker_id = args.get("existing_worker_id")

    if worker_type and worker_id:
        ref_map = {
            "applicant": ("ApplicantObjectType", "ApplicantObjectIDType", "Applicant_ID", "Applicant_Reference"),
            "former_worker": ("Former_WorkerObjectType", "Former_WorkerObjectIDType", "Former_Worker_ID", "Former_Worker_Reference"),
            "student": ("StudentObjectType", "StudentObjectIDType", "Student_ID", "Student_Reference"),
            "academic_affiliate": ("Academic_AffiliateObjectType", "Academic_AffiliateObjectIDType", "Academic_Affiliate_ID", "Academic_Affiliate_Reference"),
        }
        if worker_type not in ref_map:
            raise ValueError(
                f"Unknown existing_worker_type '{worker_type}'. Must be one of: {list(ref_map)}"
            )
        obj_type_name, id_type_name, id_type, field_name = ref_map[worker_type]
        hire_data_fields[field_name] = _ref(factory, obj_type_name, id_type_name, worker_id, id_type)

    elif args.get("first_name") or args.get("last_name"):
        # Build a brand-new Applicant_Data with Name_Data
        name_detail = factory.Person_Name_Detail_DataType(
            Country_Reference=_ref(
                factory, "CountryObjectType", "CountryObjectIDType",
                args.get("name_country_id", "USA"), "ISO_3166-1_Alpha-3_Code"
            ),
            First_Name=args.get("first_name"),
            Middle_Name=args.get("middle_name"),
            Last_Name=args.get("last_name"),
        )
        personal_data_fields = {
            "Name_Data": factory.Person_Name_DataType(
                Legal_Name_Data=factory.Legal_Name_DataType(Name_Detail_Data=name_detail)
            )
        }

        # Optional contact data
        contact_fields = {}
        if args.get("email_address"):
            contact_fields["Email_Address_Data"] = [
                factory.Email_Address_Information_DataType(Email_Address=args["email_address"])
            ]
        if args.get("phone_number"):
            contact_fields["Phone_Data"] = [
                factory.Phone_Information_DataType(
                    Country_ISO_Code=args.get("phone_country_iso_code", "US"),
                    Phone_Number=args["phone_number"],
                )
            ]
        if args.get("address_line_1"):
            contact_fields["Address_Data"] = [
                factory.Address_Information_DataType(
                    Country_Reference=_ref(
                        factory, "CountryObjectType", "CountryObjectIDType",
                        args.get("address_country_id", "USA"), "ISO_3166-1_Alpha-3_Code"
                    ),
                    Address_Line_Data=[args["address_line_1"]],
                    Municipality=args.get("address_city"),
                    Postal_Code=args.get("address_postal_code"),
                )
            ]
        if contact_fields:
            personal_data_fields["Contact_Data"] = factory.Contact_Information_DataType(**contact_fields)

        # Optional national ID (nested inside Personal_Data)
        if args.get("national_id"):
            national_id_data = factory.National_ID_DataType(
                ID=args["national_id"],
                ID_Type_Reference=_ref(
                    factory, "National_ID_TypeObjectType", "National_ID_TypeObjectIDType",
                    args.get("national_id_type", ""), "National_ID_Type_Code"
                ),
                Country_Reference=_ref(
                    factory, "CountryObjectType", "CountryObjectIDType",
                    args.get("national_id_country", "USA"), "ISO_3166-1_Alpha-3_Code"
                ),
            )
            personal_data_fields["Identification_Data"] = factory.Person_Identification_DataType(
                National_ID=[factory.National_IDType(National_ID_Data=national_id_data)]
            )

        personal_data = factory.Personal_Information_DataType(**personal_data_fields)
        applicant_data_fields = {"Personal_Data": personal_data}
        hire_data_fields["Applicant_Data"] = factory.Create_Applicant_DataType(**applicant_data_fields)
    else:
        raise ValueError(
            "Must supply either (existing_worker_type + existing_worker_id) "
            "or (first_name + last_name)."
        )

    # ---- Organization / Position / Requisition ----
    if args.get("organization_id"):
        hire_data_fields["Organization_Reference"] = _ref(
            factory, "OrganizationObjectType", "OrganizationObjectIDType",
            args["organization_id"], "Organization_Reference_ID",
        )
    if args.get("position_id"):
        hire_data_fields["Position_Reference"] = _ref(
            factory, "Position_ElementObjectType", "Position_ElementObjectIDType",
            args["position_id"], "Position_ID",
        )
    elif args.get("job_requisition_id"):
        hire_data_fields["Job_Requisition_Reference"] = _ref(
            factory, "Job_RequisitionObjectType", "Job_RequisitionObjectIDType",
            args["job_requisition_id"], "Job_Requisition_ID",
        )

    if args.get("hire_date"):
        hire_data_fields["Hire_Date"] = args["hire_date"]

    # ---- Hire_Employee_Event_Data ----
    position_details_fields = {}
    if args.get("job_profile_id"):
        position_details_fields["Job_Profile_Reference"] = _ref(
            factory, "Job_ProfileObjectType", "Job_ProfileObjectIDType",
            args["job_profile_id"], "Job_Profile_ID",
        )
    if args.get("position_title"):
        position_details_fields["Position_Title"] = args["position_title"]
    if args.get("business_title"):
        position_details_fields["Business_Title"] = args["business_title"]
    if args.get("location_id"):
        position_details_fields["Location_Reference"] = _ref(
            factory, "LocationObjectType", "LocationObjectIDType",
            args["location_id"], "Location_ID",
        )
    if args.get("time_type_id"):
        position_details_fields["Position_Time_Type_Reference"] = _ref(
            factory, "Position_Time_TypeObjectType", "Position_Time_TypeObjectIDType",
            args["time_type_id"], "Position_Time_Type_ID",
        )
    if args.get("scheduled_hours") is not None:
        position_details_fields["Scheduled_Hours"] = args["scheduled_hours"]

    event_data_fields: dict[str, Any] = {}

    # --- FIX #2: only attach Position_Details if something was actually
    # supplied, and optionally suppress it entirely when an existing
    # position_id reference was given (tenant-dependent — see toggle above).
    suppress_details = _POSITION_ID_SUPPRESSES_DETAILS and bool(args.get("position_id"))
    if position_details_fields and not suppress_details:
        event_data_fields["Position_Details"] = factory.Position_Details_Sub_DataType(**position_details_fields)
    elif position_details_fields and suppress_details:
        print(
            "[HireSOAPService] WARNING: position_id was supplied along with "
            "position-detail fields (job_profile_id/title/location/etc). "
            "Position_Details_Sub_DataType was suppressed per "
            "_POSITION_ID_SUPPRESSES_DETAILS=True — those detail fields were "
            "ignored. Flip the toggle if your tenant expects both.",
            file=sys.stderr,
        )

    if args.get("employee_type_id"):
        event_data_fields["Employee_Type_Reference"] = _ref(
            factory, "Position_Worker_TypeObjectType", "Position_Worker_TypeObjectIDType",
            args["employee_type_id"], "Employee_Type_ID",
        )
    if args.get("hire_reason_id"):
        event_data_fields["Hire_Reason_Reference"] = _ref(
            factory, "General_Event_SubcategoryObjectType", "General_Event_SubcategoryObjectIDType",
            args["hire_reason_id"], "General_Event_Subcategory_ID",
        )
    if args.get("first_day_of_work"):
        event_data_fields["First_Day_of_Work"] = args["first_day_of_work"]

    if event_data_fields:
        hire_data_fields["Hire_Employee_Event_Data"] = factory.Hire_Employee_Event_DataType(**event_data_fields)

    # ---- Compensation sub-process (optional) ----
    comp_fields = {}
    if args.get("compensation_package_id") or args.get("compensation_grade_id"):
        guideline_fields = {}
        if args.get("compensation_package_id"):
            guideline_fields["Compensation_Package_Reference"] = _ref(
                factory, "Compensation_PackageObjectType", "Compensation_PackageObjectIDType",
                args["compensation_package_id"], "Compensation_Package_ID",
            )
        if args.get("compensation_grade_id"):
            guideline_fields["Compensation_Grade_Reference"] = _ref(
                factory, "Compensation_GradeObjectType", "Compensation_GradeObjectIDType",
                args["compensation_grade_id"], "Compensation_Grade_ID",
            )
        comp_fields["Compensation_Guidelines_Data"] = factory.Compensation_Guidelines_DataType(**guideline_fields)

    if args.get("base_pay_amount") is not None:
        pay_plan_sub = factory.Pay_Plan_Sub_DataType(
            Amount=args["base_pay_amount"],
            Currency_Reference=_ref(
                factory, "CurrencyObjectType", "CurrencyObjectIDType",
                args.get("base_pay_currency_id", "USD"), "Currency_ID"
            ),
            Frequency_Reference=_ref(
                factory, "FrequencyObjectType", "FrequencyObjectIDType",
                args.get("base_pay_frequency_id", "Annual"), "Frequency_ID"
            ),
        )
        comp_fields["Pay_Plan_Data"] = factory.Pay_Plan_DataType(Pay_Plan_Sub_Data=[pay_plan_sub])

    if comp_fields:
        hire_data_fields["Propose_Compensation_for_Hire_Sub_Process"] = factory.Propose_Compensation_For_Employment_Sub_Business_ProcessType(
            Propose_Compensation_for_Employment_Data=factory.Compensation_Proposed_For_Employment_DataType(**comp_fields)
        )

    # --- FIX #3: advanced_fields (Tier 3) must not silently clobber
    # Tier 1 / Tier 2 fields that were already built from validated args.
    if args.get("advanced_fields"):
        collisions = set(args["advanced_fields"]) & set(hire_data_fields)
        if collisions:
            raise ValueError(
                "advanced_fields collides with fields already built from "
                f"structured args: {sorted(collisions)}. Remove these keys "
                "from advanced_fields or pass them through the structured "
                "arguments instead — refusing to silently overwrite "
                "validated Tier 1/2 data with an unvalidated passthrough."
            )
        hire_data_fields.update(args["advanced_fields"])

    hire_employee_data = factory.Hire_Employee_Business_Process_DataType(**hire_data_fields)

    request_kwargs = {"Hire_Employee_Data": hire_employee_data}
    if business_process_parameters is not None:
        request_kwargs["Business_Process_Parameters"] = business_process_parameters

    return request_kwargs


# ---------------------------------------------------------------------------
# 5. HireSOAPService — the only public interface
#    Called directly by src/brain/executor.py when api_type == "soap"
#    and service == "hire_employee".
# ---------------------------------------------------------------------------
class HireSOAPService:
    """
    Executes Workday SOAP Hire_Employee calls and returns a clean dict.

    Usage (from Executor):
        service = HireSOAPService()
        result  = service.hire_employee({
            "first_name":      "Jane",
            "last_name":       "Doe",
            "organization_id": "ORG-100",
            "hire_date":       "2025-08-01",
            "position_id":     "POS-5500",
            "auto_complete":   False,
        })

    TIER 1 — must provide one of:
        (existing_worker_type + existing_worker_id)  OR  (first_name + last_name)
    AND one of:
        position_id  OR  job_requisition_id
    AND always:
        organization_id, hire_date

    TIER 2 — optional: national_id, email_address, phone_number, address_*,
              compensation_*, comment, auto_complete, run_now

    TIER 3 — advanced_fields: raw dict merged directly into Hire_Employee_Data.
              Keys that collide with anything already built from Tier 1/2 args
              raise a ValueError instead of silently overwriting it.
    """

    def hire_employee(self, args: dict) -> dict:
        """
        Execute a SOAP Hire_Employee call and return a result dict.

        Returns:
            {
                "status":              "success" | "error",
                "employee_reference":  str | None,
                "message":             str          # error detail if status == "error"
            }

        Raises:
            ValueError: if required TIER 1 fields are missing, or if
                        advanced_fields collides with structured args
                        (guard check — no network call made).
            RuntimeError: if the SOAP call itself fails, either as a
                        Workday-side Fault (with .detail surfaced) or
                        a client-side/network error.
        """
        print("[HireSOAPService] Executing Hire_Employee via SOAP...", file=sys.stderr)
        print(f"[HireSOAPService] Args received: {list(args.keys())}", file=sys.stderr)

        # ── Guard: validate TIER 1 requirements before touching Workday ──
        has_existing = args.get("existing_worker_type") and args.get("existing_worker_id")
        has_new_name = args.get("first_name") and args.get("last_name")
        has_position = args.get("position_id") or args.get("job_requisition_id")

        missing = []
        if not (has_existing or has_new_name):
            missing.append("either (existing_worker_type + existing_worker_id) or (first_name + last_name)")
        if not has_position:
            missing.append("position_id or job_requisition_id")
        if not args.get("organization_id"):
            missing.append("organization_id")
        if not args.get("hire_date"):
            missing.append("hire_date")

        if missing:
            raise ValueError(
                "Hire_Employee call rejected — missing required fields: "
                + "; ".join(missing)
            )

        client = get_zeep_client()
        request_kwargs = build_hire_employee_request(client, args)

        # --- FIX #4: distinguish Workday-side Faults (with detail) from
        # client-side/network errors, instead of flattening both to str(exc).
        try:
            response = client.service.Hire_Employee(**request_kwargs)
        except Fault as exc:
            detail = getattr(exc, "detail", None)
            # Serialize lxml elements cleanly without breaking string-based test mocks
            if isinstance(detail, etree._Element):
                detail = etree.tostring(detail, encoding="unicode")
            elif detail is not None and not isinstance(detail, (str, int, float, dict, list)):
                try:
                    detail = serialize_object(detail)
                except Exception:
                    pass

            raise RuntimeError(
                f"Workday rejected Hire_Employee — {exc.message} | detail={detail}"
            ) from exc
        except Exception as exc:
            raise RuntimeError(
                f"Hire_Employee call failed before/outside a SOAP fault "
                f"(client, network, or type-factory error): {exc}"
            ) from exc

        result = {
            "status": "success",
            "employee_reference": str(getattr(response, "Employee_Reference", None)),
            "raw_response_summary": str(response)[:2000],
        }
        print(f"[HireSOAPService] Hire completed. Employee ref: {result['employee_reference']}", file=sys.stderr)
        return result
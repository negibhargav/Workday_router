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
"""

import json
import os
import sys
from typing import Any

from dotenv import load_dotenv
from zeep import Client, Settings
from zeep.wsse.username import UsernameToken
from zeep.transports import Transport
import requests

load_dotenv()

# ---------------------------------------------------------------------------
# 1. CONFIG — reads from .env; falls back to placeholder strings
# ---------------------------------------------------------------------------
WSDL_URL     = os.getenv("WORKDAY_WSDL_URL",    "https://wd5-services1.myworkday.com/ccx/service/acme/Staffing/v43.0?wsdl")
ISU_USERNAME = os.getenv("WORKDAY_ISU_USERNAME", "integration_user1@acme")
ISU_PASSWORD = os.getenv("WORKDAY_ISU_PASSWORD", "your_password_here")

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
    obj_type   = getattr(factory, obj_type_name)
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
        bpp_fields["Comment_Data"] = factory.Comment_DataType(Comment=args["comment"])
    business_process_parameters = (
        factory.Business_Process_ParametersType(**bpp_fields) if bpp_fields else None
    )

    # ---- Person identification (choice) ----
    hire_data_fields: dict[str, Any] = {}
    worker_type = args.get("existing_worker_type")
    worker_id   = args.get("existing_worker_id")

    if worker_type and worker_id:
        ref_map = {
            "applicant":          ("ApplicantObjectType",           "ApplicantObjectIDType",           "Applicant_ID",          "Applicant_Reference"),
            "former_worker":      ("Former_WorkerObjectType",       "Former_WorkerObjectIDType",       "Former_Worker_ID",      "Former_Worker_Reference"),
            "student":            ("StudentObjectType",             "StudentObjectIDType",             "Student_ID",            "Student_Reference"),
            "academic_affiliate": ("Academic_AffiliateObjectType", "Academic_AffiliateObjectIDType", "Academic_Affiliate_ID", "Academic_Affiliate_Reference"),
        }
        obj_type_name, id_type_name, id_type, field_name = ref_map[worker_type]
        hire_data_fields[field_name] = _ref(factory, obj_type_name, id_type_name, worker_id, id_type)

    elif args.get("first_name") or args.get("last_name"):
        # Build a brand-new Applicant_Data with Name_Data
        name_detail = factory.Name_Detail_DataType(
            Country_Reference=_ref(factory, "CountryObjectType", "CountryObjectIDType",
                                    args.get("name_country_id", "USA"), "ISO_3166-1_Alpha-3_Code"),
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
                factory.Email_Address_DataType(Email_Address=args["email_address"])
            ]
        if args.get("phone_number"):
            contact_fields["Phone_Data"] = [
                factory.Phone_DataType(
                    Country_ISO_Code=args.get("phone_country_iso_code", "US"),
                    Phone_Number=args["phone_number"],
                )
            ]
        if args.get("address_line_1"):
            contact_fields["Address_Data"] = [
                factory.Address_DataType(
                    Country_Reference=_ref(factory, "CountryObjectType", "CountryObjectIDType",
                                            args.get("address_country_id", "USA"), "ISO_3166-1_Alpha-3_Code"),
                    Address_Line_Data=[args["address_line_1"]],
                    Municipality=args.get("address_city"),
                    Postal_Code=args.get("address_postal_code"),
                )
            ]
        if contact_fields:
            personal_data_fields["Contact_Data"] = factory.Contact_DataType(**contact_fields)

        applicant_data_fields = {"Personal_Data": factory.Personal_DataType(**personal_data_fields)}

        # Optional national ID (sibling of Personal_Data)
        if args.get("national_id"):
            national_id_data = factory.National_ID_DataType(
                ID=args["national_id"],
                ID_Type_Reference=_ref(factory, "National_ID_TypeObjectType", "National_ID_TypeObjectIDType",
                                        args.get("national_id_type", ""), "National_ID_Type_Code"),
                Country_Reference=_ref(factory, "CountryObjectType", "CountryObjectIDType",
                                        args.get("national_id_country", "USA"), "ISO_3166-1_Alpha-3_Code"),
            )
            applicant_data_fields["Identification_Data"] = factory.Identification_DataType(
                National_ID=[factory.National_IDType(National_ID_Data=national_id_data)]
            )

        hire_data_fields["Applicant_Data"] = factory.Applicant_DataType(**applicant_data_fields)

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

    event_data_fields = {"Position_Details": factory.Position_Details_Sub_DataType(**position_details_fields)}
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
            Currency_Reference=_ref(factory, "CurrencyObjectType", "CurrencyObjectIDType",
                                     args.get("base_pay_currency_id", "USD"), "Currency_ID"),
            Frequency_Reference=_ref(factory, "FrequencyObjectType", "FrequencyObjectIDType",
                                      args.get("base_pay_frequency_id", "Annual"), "Frequency_ID"),
        )
        comp_fields["Pay_Plan_Data"] = factory.Pay_Plan_DataType(Pay_Plan_Sub_Data=[pay_plan_sub])

    if comp_fields:
        hire_data_fields["Propose_Compensation_for_Hire_Sub_Process"] = factory.Propose_Compensation_for_Hire_Sub_ProcessType(
            Propose_Compensation_for_Hire_Data=factory.Propose_Compensation_for_Hire_DataType(**comp_fields)
        )

    # ---- Tier 3: merge raw advanced_fields on top ----
    if args.get("advanced_fields"):
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

    TIER 3 — advanced_fields: raw dict merged directly into Hire_Employee_Data
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
            ValueError: if required TIER 1 fields are missing (guard check)
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

        try:
            response = client.service.Hire_Employee(**request_kwargs)
        except Exception as exc:
            raise RuntimeError(f"SOAP Hire_Employee call failed: {exc}") from exc

        result = {
            "status": "success",
            "employee_reference": str(getattr(response, "Employee_Reference", None)),
            "raw_response_summary": str(response)[:2000],
        }
        print(f"[HireSOAPService] Hire completed. Employee ref: {result['employee_reference']}", file=sys.stderr)
        return result
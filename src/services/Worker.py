"""
Workday Get_Workers — SOAP service module.

Provides WorkerSOAPService, a plain Python class called directly by the
Executor pipeline. No MCP, no stdio server, no asyncio — just a clean
wrapper around the Workday Staffing SOAP API.

DESIGN PRINCIPLE
-----------------
Every filter and every response-data field is OPTIONAL.
The Planner LLM decides which arguments to populate based on the user's
question. This module never hardcodes "which filters to use" -- it builds
a SOAP request from whatever args arrive and leaves everything else at
the WSDL's own defaults.

The ~60 Response_Group booleans (Include_Compensation, Include_Photo, ...)
are passed in as a list under the `include_fields` key. The Planner picks
whichever names are relevant to the question.

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
WSDL_URL     = os.getenv("WORKDAY_WSDL_URL",     "https://wd5-services1.myworkday.com/ccx/service/acme/Staffing/v43.0?wsdl")
ISU_USERNAME = os.getenv("WORKDAY_ISU_USERNAME",  "integration_user1@acme")
ISU_PASSWORD = os.getenv("WORKDAY_ISU_PASSWORD",  "your_password_here")

# ---------------------------------------------------------------------------
# 2. ALL 60 RESPONSE_GROUP FLAGS (from the Workday Staffing WSDL)
# ---------------------------------------------------------------------------
RESPONSE_GROUP_FIELDS = [
    "Include_Reference", "Include_Personal_Information", "Show_All_Personal_Information",
    "Include_Additional_Jobs", "Include_Employment_Information", "Include_Compensation",
    "Include_Organizations", "Exclude_Organization_Support_Role_Data",
    "Exclude_Location_Hierarchies", "Exclude_Cost_Centers", "Exclude_Cost_Center_Hierarchies",
    "Exclude_Companies", "Exclude_Company_Hierarchies", "Exclude_Matrix_Organizations",
    "Exclude_Pay_Groups", "Exclude_Regions", "Exclude_Region_Hierarchies",
    "Exclude_Supervisory_Organizations", "Exclude_Teams", "Exclude_Custom_Organizations",
    "Include_Roles", "Include_Management_Chain_Data",
    "Include_Multiple_Managers_in_Management_Chain_Data", "Include_Benefit_Enrollments",
    "Include_Benefit_Eligibility", "Include_Related_Persons", "Include_Qualifications",
    "Include_Employee_Review", "Include_Goals", "Include_Development_Items",
    "Include_Skills", "Include_Photo", "Include_Worker_Documents",
    "Include_Transaction_Log_Data", "Include_Subevents_for_Corrected_Transaction",
    "Include_Subevents_for_Rescinded_Transaction", "Include_Succession_Profile",
    "Include_Talent_Assessment", "Include_Employee_Contract_Data",
    "Include_Contracts_for_Terminated_Workers", "Include_Collective_Agreement_Data",
    "Include_Probation_Period_Data", "Include_Extended_Employee_Contract_Details",
    "Include_Feedback_Received", "Include_User_Account", "Include_Career",
    "Include_Account_Provisioning", "Include_Background_Check_Data",
    "Include_Contingent_Worker_Tax_Authority_Form_Information", "Exclude_Funds",
    "Exclude_Fund_Hierarchies", "Exclude_Grants", "Exclude_Grant_Hierarchies",
    "Exclude_Business_Units", "Exclude_Business_Unit_Hierarchies", "Exclude_Programs",
    "Exclude_Program_Hierarchies", "Exclude_Gifts", "Exclude_Gift_Hierarchies",
    "Exclude_Retiree_Organizations",
]

# ---------------------------------------------------------------------------
# 3. ZEEP CLIENT SETUP — built once, reused across calls (singleton)
# ---------------------------------------------------------------------------
def build_zeep_client() -> Client:
    session = requests.Session()
    transport = Transport(session=session, timeout=30)
    settings = Settings(xml_huge_tree=True)
    return Client(
        wsdl=WSDL_URL,
        wsse=UsernameToken(ISU_USERNAME, ISU_PASSWORD),
        transport=transport,
        settings=settings,
    )


_zeep_client: Client | None = None


def get_zeep_client() -> Client:
    global _zeep_client
    if _zeep_client is None:
        _zeep_client = build_zeep_client()
    return _zeep_client


# ---------------------------------------------------------------------------
# 4. DYNAMIC REQUEST BUILDER
#    Only sets fields that were actually provided in `args`.
#    Nothing here is scenario-specific — it's a generic mapper.
# ---------------------------------------------------------------------------
def build_get_workers_request(client: Client, args: dict[str, Any]) -> dict:
    factory = client.type_factory("urn:com.workday/bsvc")
    request_kwargs: dict[str, Any] = {}

    # --- Request_References ---
    worker_ids = args.get("worker_ids")
    if worker_ids:
        refs = [
            factory.WorkerObjectType(
                ID=[factory.WorkerObjectIDType(_value_1=wid, type="Employee_ID")]
            )
            for wid in worker_ids
        ]
        request_kwargs["Request_References"] = factory.WorkersRequestReferencesType(
            Worker_Reference=refs
        )

    # --- Request_Criteria (only build it if at least one criterion given) ---
    criteria_fields = {}

    if args.get("organization_id"):
        criteria_fields["Organization_Reference"] = factory.OrganizationObjectType(
            ID=[factory.OrganizationObjectIDType(
                _value_1=args["organization_id"], type="Organization_Reference_ID")]
        )
    if "include_subordinate_organizations" in args:
        criteria_fields["Include_Subordinate_Organizations"] = args["include_subordinate_organizations"]
    if args.get("country_id"):
        criteria_fields["Country_Reference"] = factory.CountryObjectType(
            ID=[factory.CountryObjectIDType(_value_1=args["country_id"], type="ISO_3166-1_Alpha-2_Code")]
        )
    if args.get("position_id"):
        criteria_fields["Position_Reference"] = factory.Position_ElementObjectType(
            ID=[factory.Position_ElementObjectIDType(_value_1=args["position_id"], type="Position_ID")]
        )
    if args.get("national_id"):
        criteria_fields["National_ID_Criteria_Data"] = factory.Worker_by_National_ID_Request_CriteriaType(
            Identifier_ID=args["national_id"],
            National_ID_Type_Reference=factory.National_ID_TypeObjectType(
                ID=[factory.National_ID_TypeObjectIDType(
                    _value_1=args.get("national_id_type", ""), type="National_ID_Type_Code")]
            ) if args.get("national_id_type") else None,
            Country_Reference=factory.CountryObjectType(
                ID=[factory.CountryObjectIDType(
                    _value_1=args.get("national_id_country", ""), type="ISO_3166-1_Alpha-2_Code")]
            ) if args.get("national_id_country") else None,
        )
    if "exclude_inactive_workers" in args:
        criteria_fields["Exclude_Inactive_Workers"] = args["exclude_inactive_workers"]
    if "exclude_employees" in args:
        criteria_fields["Exclude_Employees"] = args["exclude_employees"]
    if "exclude_contingent_workers" in args:
        criteria_fields["Exclude_Contingent_Workers"] = args["exclude_contingent_workers"]

    date_range_fields = {}
    for key, wsdl_name in (
        ("updated_from",    "Updated_From"),
        ("updated_through", "Updated_Through"),
        ("effective_from",  "Effective_From"),
        ("effective_through", "Effective_Through"),
    ):
        if args.get(key):
            date_range_fields[wsdl_name] = args[key]
    if date_range_fields:
        criteria_fields["Transaction_Log_Criteria_Data"] = factory.Transaction_Log_CriteriaType(
            Transaction_Date_Range_Data=factory.Effective_And_Updated_DateTime_DataType(**date_range_fields)
        )

    if criteria_fields:
        request_kwargs["Request_Criteria"] = factory.Worker_Request_CriteriaType(**criteria_fields)

    # --- Response_Filter ---
    filter_fields = {}
    for key, wsdl_name in (
        ("as_of_effective_date",  "As_Of_Effective_Date"),
        ("as_of_entry_datetime",  "As_Of_Entry_DateTime"),
        ("page",                  "Page"),
        ("count",                 "Count"),
    ):
        if args.get(key) is not None:
            filter_fields[wsdl_name] = args[key]
    if filter_fields:
        request_kwargs["Response_Filter"] = factory.Response_FilterType(**filter_fields)

    # --- Response_Group ---
    include_fields = args.get("include_fields")
    if include_fields:
        group_fields = {name: True for name in include_fields}
        request_kwargs["Response_Group"] = factory.Worker_Response_GroupType(**group_fields)

    return request_kwargs


# ---------------------------------------------------------------------------
# 5. WorkerSOAPService — the only public interface
#    Called directly by src/brain/executor.py when api_type == "soap".
# ---------------------------------------------------------------------------
class WorkerSOAPService:
    """
    Executes Workday SOAP Get_Workers calls and returns a clean dict.

    Usage (from Executor):
        service = WorkerSOAPService()
        result  = service.get_workers({
            "worker_ids":    ["21008"],
            "include_fields": ["Include_Compensation", "Include_Skills"],
        })
        # result → {"count_returned": 1, "workers": [{"id": ..., "descriptor": ...}]}

    All parameters are optional — pass only what the Planner supplied.
    Any unrecognised key in `args` is silently ignored.
    """

    def get_workers(self, args: dict) -> dict:
        """
        Execute a SOAP Get_Workers call and return a clean dict.

        Args:
            args: dict with any of these optional keys:
                  worker_ids, organization_id, include_subordinate_organizations,
                  country_id, position_id, national_id, national_id_type,
                  national_id_country, exclude_inactive_workers, exclude_employees,
                  exclude_contingent_workers, updated_from, updated_through,
                  effective_from, effective_through, as_of_effective_date,
                  as_of_entry_datetime, page, count, include_fields

        Returns:
            {
                "count_returned": int,
                "workers": [
                    {"id": str, "descriptor": str},
                    ...
                ]
            }

        Raises:
            RuntimeError: if the SOAP call itself fails
        """
        print("[WorkerSOAPService] Executing Get_Workers via SOAP...", file=sys.stderr)
        print(f"[WorkerSOAPService] Args received: {list(args.keys())}", file=sys.stderr)

        client = get_zeep_client()
        request_kwargs = build_get_workers_request(client, args)

        try:
            response = client.service.Get_Workers(**request_kwargs)
        except Exception as exc:
            raise RuntimeError(f"SOAP Get_Workers call failed: {exc}") from exc

        workers_out = []
        for worker in getattr(response.Response_Data, "Worker", []) or []:
            workers_out.append({
                "id": (
                    worker.Worker_Reference.ID[0]._value_1
                    if worker.Worker_Reference.ID else None
                ),
                "descriptor": worker.Worker_Reference.Descriptor,
            })

        result = {
            "count_returned": len(workers_out),
            "workers": workers_out,
        }
        print(
            f"[WorkerSOAPService] Returned {result['count_returned']} worker(s).",
            file=sys.stderr,
        )
        return result
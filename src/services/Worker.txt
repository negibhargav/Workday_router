"""
Workday Get_Workers exposed as an MCP tool.

DESIGN PRINCIPLE
-----------------
Every filter and every response-data field is OPTIONAL in the tool schema.
The calling LLM (Claude, etc.) reads the user's natural-language question
and decides at call-time which arguments to populate. This backend never
hardcodes "which filters to use" -- it just builds a SOAP request out of
whatever arguments actually arrived, and leaves everything else at the
WSDL's own defaults.

The ~60 Response_Group booleans (Include_Compensation, Include_Photo, ...)
are NOT exposed as 60 separate parameters -- that's unusable for an LLM
function-calling schema. Instead they're exposed as ONE array parameter,
`include_fields`, whose items are an enum of the 60 valid names. The LLM
just picks whichever names are relevant to the question.

INSTALL
-------
    pip install mcp zeep requests

RUN (stdio transport, e.g. from Claude Desktop's mcp config)
--------------------------------------------------------------
    python workday_mcp_server.py
"""

import asyncio
import json
from typing import Any

from zeep import Client, Settings
from zeep.wsse.username import UsernameToken
from zeep.transports import Transport
import requests

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# ---------------------------------------------------------------------------
# 1. CONFIG -- fill these in for your tenant
# ---------------------------------------------------------------------------
WSDL_URL = "https://wd5-services1.myworkday.com/ccx/service/acme/Staffing/v43.0?wsdl"
ISU_USERNAME = "integration_user1@acme"
ISU_PASSWORD = "your_password_here"

# All 60 Response_Group flags, pulled straight from the WSDL schema.
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
# 2. THE TOOL SCHEMA -- everything optional, this is what the LLM sees
# ---------------------------------------------------------------------------
GET_WORKERS_TOOL = Tool(
    name="get_workers",
    description=(
        "Look up Workday workers. All parameters are optional -- only "
        "include the ones relevant to the user's question. Use "
        "worker_ids for specific people; use the other filters for a "
        "broader search; use include_fields to request specific data "
        "sections (compensation, org data, photo, skills, etc.) -- "
        "requesting fewer fields returns faster, smaller responses."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            # --- Request_References ---
            "worker_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Specific worker Employee_IDs to fetch.",
            },
            # --- Request_Criteria ---
            "organization_id": {
                "type": "string",
                "description": "Limit to workers in this supervisory organization.",
            },
            "include_subordinate_organizations": {
                "type": "boolean",
                "description": "Also include workers in orgs beneath organization_id.",
            },
            "country_id": {
                "type": "string",
                "description": "Limit to workers in this country.",
            },
            "position_id": {
                "type": "string",
                "description": "Limit to the worker in this position.",
            },
            "national_id": {
                "type": "string",
                "description": "Look up a worker by national ID value.",
            },
            "national_id_type": {
                "type": "string",
                "description": "The type of the national_id (e.g. SSN, Passport).",
            },
            "national_id_country": {
                "type": "string",
                "description": "Country the national_id belongs to.",
            },
            "exclude_inactive_workers": {
                "type": "boolean",
                "description": "Exclude terminated employees / ended contingent workers.",
            },
            "exclude_employees": {
                "type": "boolean",
                "description": "Exclude all employees from results.",
            },
            "exclude_contingent_workers": {
                "type": "boolean",
                "description": "Exclude all contingent workers from results.",
            },
            "updated_from": {
                "type": "string",
                "description": "ISO datetime. Only workers updated on/after this.",
            },
            "updated_through": {
                "type": "string",
                "description": "ISO datetime. Only workers updated on/before this.",
            },
            "effective_from": {
                "type": "string",
                "description": "ISO datetime. Effective-date range start.",
            },
            "effective_through": {
                "type": "string",
                "description": "ISO datetime. Effective-date range end.",
            },
            # --- Response_Filter ---
            "as_of_effective_date": {
                "type": "string",
                "description": "ISO date. View data as of this effective date.",
            },
            "as_of_entry_datetime": {
                "type": "string",
                "description": "ISO datetime. View data as it was entered as of this moment.",
            },
            "page": {
                "type": "integer",
                "description": "Page number of results (default 1).",
            },
            "count": {
                "type": "integer",
                "description": "Results per page, 1-999 (default 100).",
            },
            # --- Response_Group ---
            "include_fields": {
                "type": "array",
                "items": {"type": "string", "enum": RESPONSE_GROUP_FIELDS},
                "description": (
                    "Which data sections to include in the response for "
                    "each worker. Only request what's needed for the "
                    "user's question."
                ),
            },
        },
        # Nothing is "required" -- the LLM may call this with zero args
        # (which just returns page 1 of all workers with default fields).
        "required": [],
    },
)

# ---------------------------------------------------------------------------
# 3. ZEEP CLIENT SETUP (built once, reused across calls)
# ---------------------------------------------------------------------------
def build_zeep_client() -> Client:
    session = requests.Session()
    transport = Transport(session=session, timeout=30)
    settings = Settings(strict=False, xml_huge_tree=True)
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
#    Nothing here is scenario-specific -- it's a generic mapper.
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
        ("updated_from", "Updated_From"),
        ("updated_through", "Updated_Through"),
        ("effective_from", "Effective_From"),
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
        ("as_of_effective_date", "As_Of_Effective_Date"),
        ("as_of_entry_datetime", "As_Of_Entry_DateTime"),
        ("page", "Page"),
        ("count", "Count"),
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
# 5. MCP SERVER WIRING
# ---------------------------------------------------------------------------
server = Server("workday-staffing")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [GET_WORKERS_TOOL]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name != "get_workers":
        raise ValueError(f"Unknown tool: {name}")

    client = get_zeep_client()
    request_kwargs = build_get_workers_request(client, arguments or {})

    try:
        response = client.service.Get_Workers(**request_kwargs)
    except Exception as e:
        return [TextContent(type="text", text=f"Workday call failed: {e}")]

    # Serialize a trimmed, readable summary rather than the full zeep tree
    workers_out = []
    for worker in getattr(response.Response_Data, "Worker", []) or []:
        workers_out.append({
            "id": worker.Worker_Reference.ID[0]._value_1 if worker.Worker_Reference.ID else None,
            "descriptor": worker.Worker_Reference.Descriptor,
        })

    result = {
        "count_returned": len(workers_out),
        "workers": workers_out,
    }
    return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
import json
import os
from pathlib import Path
import sys

def parse_workday_swagger(file_path, output_name="workers_apis.json"):
    # Define paths
    input_file = Path(file_path)
    output_dir = Path("data/processed_intents")
    output_dir.mkdir(parents=True, exist_ok=True)

    if not input_file.exists():
        print(f"Error: Could not find {file_path}", file=sys.stderr)
        return

    with open(input_file, 'r', encoding='utf-8') as f:
        swagger_data = json.load(f)

    # 1. Extract Global Context
    base_path = swagger_data.get("basePath", "")
    paths = swagger_data.get("paths", {})
    processed_apis = []

    print(f"Searching for 'workers' APIs in {input_file.name}...", file=sys.stderr)

    # 2. Iterate through paths
    for path, methods in paths.items():
        for method, details in methods.items():
            tags = details.get("tags", [])
            if "workers" in tags:
                summary = details.get("summary", "")
                description = details.get("description", "")
                
                # 4. Minify Parameters
                raw_params = details.get("parameters", [])
                minified_params = []
                
                for p in raw_params:
                    # We only care about name, location, and requirement
                    minified_params.append({
                        "name": p.get("name"),
                        "in": p.get("in"), # path, query, or body
                        "required": p.get("required", False),
                        "type": p.get("type", "string")
                    })

                # 5. Build the Router Payload
                import hashlib
                path_method_str = f"{method.upper()}:{path}"
                unique_id = hashlib.md5(path_method_str.encode('utf-8')).hexdigest()

                api_entry = {
                    "id": unique_id,
                    "api_name": summary.lower().replace(" ", "_").strip("."),
                    "method": method.upper(),
                    "full_path": f"{base_path}{path}",
                    "parameters": minified_params,
                    "summary": summary,
                    # We leave intent_triggers empty for you to fill or automate next
                    "intent_triggers": generate_initial_intents(summary)
                }
                
                processed_apis.append(api_entry)

    # 6. Save the results
    output_path = output_dir / output_name
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(processed_apis, f, indent=4)

    print(f"Success! Extracted {len(processed_apis)} APIs to {output_path}", file=sys.stderr)

def generate_initial_intents(summary):
    """
    A helper to create basic intent triggers from the summary.
    You will likely want to refine these manually or with an LLM.
    """
    if not summary:
        return []
    
    clean_summary = summary.strip(".")
    base_intents = [
        clean_summary,
        f"How do I {clean_summary.lower()}?",
        f"I need to {clean_summary.lower()}",
        f"Can you {clean_summary.lower()}?"
    ]
    
    lower_summary = clean_summary.lower()
    if "retrieves a worker instance" in lower_summary:
        base_intents.extend([
            "what is my name",
            "who am I",
            "show my profile",
            "get worker profile",
            "retrieve my worker details",
            "who is employee",
            "get employee profile",
            "information about me",
            "my user details",
            "show my employee details"
        ])
    elif "retrieves a collection of workers" in lower_summary:
        base_intents.extend([
            "list all employees",
            "get all workers",
            "search for workers",
            "show me all workers",
            "find workers",
            "list of workers"
        ])
        
    return base_intents

if __name__ == "__main__":
    # Point this to your swagger file location
    parse_workday_swagger("swagger/common_v1.json")
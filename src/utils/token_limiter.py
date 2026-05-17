import json

def clean_workday_response(data, max_items=5, max_chars=8000, required_fields=None):
    """
    Cleans and truncates Workday API responses to save tokens.
    
    1. Removes 'metadata', 'links', and 'context' fields.
    2. Limits lists to `max_items`.
    3. If `required_fields` is provided, filters dicts to only include those fields 
       (while preserving container keys like 'data' or 'entries').
    4. Truncates final string cleanly to avoid throwing raw invalid JSON configurations.
    """
    if not isinstance(data, (dict, list)):
        return str(data)

    def _recursive_clean(obj):
        if isinstance(obj, dict):
            # Fields we always remove
            excluded = {"metadata", "links", "context", "@odata.context"}
            keys_to_keep = set(obj.keys()) - excluded
            
            # If specific fields are requested, we filter the keys
            # BUT we preserve "container" keys that hold the actual data objects
            container_keys = {"data", "entries", "items", "results"}
            
            if required_fields:
                lower_req = [f.lower() for f in required_fields]
                # Keep keys if match criteria, are standard IDs, or match a structural root container
                keys_to_keep = {
                    k for k in keys_to_keep 
                    if (k.lower() in lower_req or 
                        k.lower() in ("id", "descriptor", "workerid", "id_") or
                        k.lower() in container_keys)
                }

            cleaned = {
                k: _recursive_clean(obj[k]) 
                for k in keys_to_keep
            }
            return cleaned
            
        elif isinstance(obj, list):
            # Limit collection size to save baseline contextual layout tokens
            limited_list = obj[:max_items]
            return [_recursive_clean(item) for item in limited_list]
            
        return obj

    cleaned_data = _recursive_clean(data)
    
    # If the filtering resulted in an empty object, provide informative trace context instead of blank JSON
    if not cleaned_data and data:
        return json.dumps({
            "note": "No fields matched requested payload criteria.", 
            "available_keys": list(data.keys()) if isinstance(data, dict) else "list_collection"
        }, indent=2)

    # Convert finalized structured object to readable format 
    response_str = json.dumps(cleaned_data, indent=2)
    
    # Safe truncation check block
    if len(response_str) > max_chars:
        # If string manipulation cuts off JSON mid-air, try reducing total item caps aggressively
        if max_items > 1:
            return clean_workday_response(data, max_items=max_items - 2, max_chars=max_chars, required_fields=required_fields)
        
        # Hard fallback string truncation safety boundary wrapper
        return response_str[:max_chars] + "\n... [DATA TRUNCATED: INVALID RAW TRAILING JSON BOUNDARY] ..."
    
    return response_str
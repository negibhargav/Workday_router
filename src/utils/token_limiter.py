import json

def clean_workday_response(data, max_items=5, max_chars=8000, required_fields=None):
    """
    Cleans and truncates Workday API responses to save tokens.
    
    1. Removes 'metadata', 'links', and 'context' fields.
    2. Limits lists to `max_items`.
    3. If `required_fields` is provided, filters dicts to only include those fields 
       (while preserving container keys like 'data' or 'entries').
    4. Truncates final string to `max_chars`.
    """
    if not isinstance(data, (dict, list)):
        return str(data)

    def _recursive_clean(obj, is_top_level=False):
        if isinstance(obj, dict):
            # Fields we always remove
            excluded = {"metadata", "links", "context", "@odata.context"}
            keys_to_keep = set(obj.keys()) - excluded
            
            # If specific fields are requested, we filter the keys
            # BUT we preserve "container" keys that hold the actual data
            container_keys = {"data", "entries", "items", "results"}
            
            if required_fields:
                lower_req = [f.lower() for f in required_fields]
                # We keep keys if:
                # 1. They are in the required list
                # 2. They are common identifiers (id, descriptor)
                # 3. They are container keys (to avoid stripping the whole response)
                keys_to_keep = {
                    k for k in keys_to_keep 
                    if (k.lower() in lower_req or 
                        k.lower() in ("id", "descriptor", "workerid") or
                        k.lower() in container_keys)
                }

            cleaned = {
                k: _recursive_clean(obj[k]) 
                for k in keys_to_keep
            }
            return cleaned
            
        elif isinstance(obj, list):
            # Limit list size
            limited_list = obj[:max_items]
            return [_recursive_clean(item) for item in limited_list]
        return obj

    cleaned_data = _recursive_clean(data, is_top_level=True)
    
    # If the filtering resulted in an empty object, returning a hint might be better
    if not cleaned_data and data:
        return json.dumps({"note": "No fields matched requested criteria", "available_fields": list(data.keys()) if isinstance(data, dict) else "list"})

    # Convert to string and truncate
    response_str = json.dumps(cleaned_data, indent=2)
    
    if len(response_str) > max_chars:
        return response_str[:max_chars] + "\n... [DATA TRUNCATED TO SAVE TOKENS] ..."
    
    return response_str

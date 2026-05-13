import sys
import os
import json

# Add src to path
sys.path.append(os.path.join(os.getcwd()))

from src.utils.token_limiter import clean_workday_response

def test_token_limiter():
    # Mock Workday data
    mock_data = {
        "metadata": {"total": 100, "offset": 0},
        "links": [{"rel": "self", "href": "..."}],
        "data": [
            {"id": 1, "name": "Worker 1", "extra": "info"},
            {"id": 2, "name": "Worker 2", "extra": "info"},
            {"id": 3, "name": "Worker 3", "extra": "info"},
            {"id": 4, "name": "Worker 4", "extra": "info"},
            {"id": 5, "name": "Worker 5", "extra": "info"},
            {"id": 6, "name": "Worker 6", "extra": "info"},
        ],
        "context": "some context"
    }
    
    print("--- Original Data Keys ---")
    print(list(mock_data.keys()))
    print(f"Original Data Length (list): {len(mock_data['data'])}")
    
    # Test cleaning
    cleaned = clean_workday_response(mock_data, max_items=2)
    cleaned_dict = json.loads(cleaned.split("\n...")[0]) # Remove truncation suffix for parsing if present
    
    print("\n--- Cleaned Data Keys ---")
    print(list(cleaned_dict.keys()))
    
    print("\n--- Cleaned List Length ---")
    print(f"Cleaned Data Length (list): {len(cleaned_dict['data'])}")
    
    # Verify metadata, links, context are gone
    for key in ["metadata", "links", "context"]:
        if key in cleaned_dict:
            print(f"FAILED: {key} still in cleaned data")
        else:
            print(f"SUCCESS: {key} removed")
            
    # Test field selection
    print("\n--- Field Selection Test (Required: name) ---")
    field_cleaned = clean_workday_response(mock_data, required_fields=["name"])
    field_cleaned_dict = json.loads(field_cleaned)
    first_worker = field_cleaned_dict["data"][0]
    print(f"First worker fields: {list(first_worker.keys())}")
    if "name" in first_worker and "extra" not in first_worker:
        print("SUCCESS: Only 'name' (and id/descriptor) kept")
    else:
        print("FAILED: Field filtering incorrect")

    # Test truncation
    long_data = {"text": "A" * 10000}
    truncated = clean_workday_response(long_data, max_chars=100)
    print("\n--- Truncation Test ---")
    print(f"Truncated Length: {len(truncated)}")
    if "[DATA TRUNCATED TO SAVE TOKENS]" in truncated:
        print("SUCCESS: Truncation message found")
    else:
        print("FAILED: Truncation message not found")

if __name__ == "__main__":
    test_token_limiter()

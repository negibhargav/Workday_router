import csv
import os
import sys
import difflib
from typing import Dict, List, Optional, Tuple

class ReferenceResolver:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(ReferenceResolver, cls).__new__(cls, *args, **kwargs)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        
        # Determine the references directory path
        self.ref_dir = os.path.abspath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "..", "data", "References"
        ))
        
        # Cache for loaded reference data: { filename: [ (id, note, normalized_note, normalized_id) ] }
        self._cache: Dict[str, List[Tuple[str, str, str, str]]] = {}
        
        # Map parameters to their specific CSV filenames
        self.param_to_file = {
            "location_id": "get_references_location_id.csv",
            "location": "get_references_location_id.csv",
            "employee_type_id": "get_references_employee_type_id.csv",
            "employee_type": "get_references_employee_type_id.csv",
            "contingent_worker_type_id": "get_references_contingent_worker_type_id.csv",
            "existing_worker_type": "get_references_contingent_worker_type_id.csv",
            "pay_rate_type_id": "get_references_pay_rate_type_id.csv",
            "pay_rate_type": "get_references_pay_rate_type_id.csv",
            "frequency_id": "get_references_frequency_id.csv",
            "frequency": "get_references_frequency_id.csv",
            "base_pay_frequency_id": "get_references_frequency_id.csv",
            "recruiting_stage_id": "get_references_recruiting_stage_id.csv",
            "recruiting_stage": "get_references_recruiting_stage_id.csv",
            "management_level_id": "get_references_management_level_id.csv",
            "management_level": "get_references_management_level_id.csv",
            "job_profile_id": "get_references_job_profile_id.csv",
            "job_profile": "get_references_job_profile_id.csv",
            "job_requisition_id": "get_references_job_requisition_id.csv",
            "job_requisition": "get_references_job_requisition_id.csv",
            "job_requisition_status_id": "get_references_job_requisition_status_id.csv",
            "change_job_category_id": "get_references_change_job_category_id.csv",
            "change_job_subcategory_id": "get_references_change_job_subcategory_id.csv",
            "company_id": "get_references_company_reference_id.csv",
            "company_reference_id": "get_references_company_reference_id.csv",
            "organization_id": "get_references_organization_reference_id.csv",
            "organization_reference_id": "get_references_organization_reference_id.csv",
            "position_id": "get_references_position_id.csv",
            "position": "get_references_position_id.csv",
            "position_time_type_id": "get_references_position_time_type_id.csv",
            "phone_device_type_id": "get_references_phone_device_type_id.csv",
            "communication_usage_type_id": "get_references_communication_usage_type_id.csv",
        }

    def _normalize(self, text: str) -> str:
        """Helper to lowercase, strip, and replace underscores/hyphens/slashes/punctuation with spaces."""
        if not text:
            return ""
        t = text.lower().strip()
        for char in ("_", "-", "/", "\\", "(", ")", "[", "]", "{", "}", ",", "."):
            t = t.replace(char, " ")
        # collapse multiple spaces
        return " ".join(t.split())

    def _load_file(self, filename: str) -> List[Tuple[str, str, str, str]]:
        """Loads and parses the CSV file from references folder, caching the results."""
        if filename in self._cache:
            return self._cache[filename]

        filepath = os.path.join(self.ref_dir, filename)
        if not os.path.exists(filepath):
            # Try to resolve case insensitivity or check if it exists in directory
            print(f"[ReferenceResolver] Warning: File not found: {filepath}", file=sys.stderr)
            return []

        rows = []
        try:
            with open(filepath, mode="r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Filter active reference records
                    active_str = str(row.get("active", "")).strip().lower()
                    if active_str not in ("true", "1", "yes", ""):
                        continue
                    
                    ref_id = row.get("id")
                    ref_note = row.get("note")
                    if ref_id and ref_note:
                        rows.append((
                            ref_id,
                            ref_note,
                            self._normalize(ref_note),
                            self._normalize(ref_id)
                        ))
        except Exception as e:
            print(f"[ReferenceResolver] Error loading reference file {filename}: {e}", file=sys.stderr)

        self._cache[filename] = rows
        return rows

    def _find_matching_filename(self, param_key: str) -> Optional[str]:
        """Tries to find the CSV file mapping to a given parameter key."""
        key = param_key.lower().strip()
        if key in self.param_to_file:
            return self.param_to_file[key]
        
        # Dynamic matching helper: e.g., "custom_organization_id" or "my_location"
        # Search if any key is a substring or if we can construct one
        # If the parameter ends with _id, try to strip it and check files
        candidates = []
        if key.endswith("_id"):
            base = key[:-3]
            candidates.append(f"get_references_{base}_id.csv")
            candidates.append(f"get_references_{base}.csv")
        candidates.append(f"get_references_{key}_id.csv")
        candidates.append(f"get_references_{key}.csv")
        
        for cand in candidates:
            if os.path.exists(os.path.join(self.ref_dir, cand)):
                return cand
                
        return None

    def resolve(self, param_key: str, value: str) -> Optional[str]:
        """
        Resolves a natural language value to a Workday ID from references.
        
        Args:
            param_key: The parameter name (e.g. 'location_id')
            value: The natural language description (e.g. 'San Francisco office')
            
        Returns:
            The resolved reference ID if found, or None if no match is confident enough.
        """
        if not value or not isinstance(value, str):
            return None

        # Clean/normalize values for comparison
        clean_val = value.strip()
        if not clean_val:
            return None

        filename = self._find_matching_filename(param_key)
        if not filename:
            return None

        rows = self._load_file(filename)
        if not rows:
            return None

        # 1. First Pass: Check for exact matches (either note or ID)
        norm_val = self._normalize(clean_val)
        for ref_id, ref_note, norm_note, norm_id in rows:
            if norm_val == norm_note or norm_val == norm_id:
                print(f"[ReferenceResolver] Exact match found in {filename}: '{clean_val}' -> '{ref_id}'", file=sys.stderr)
                return ref_id

        # 2. Second Pass: Check for substring/contains matches (either direction)
        # We want to match "San Francisco office" with "San Francisco" or vice versa
        best_substring_match = None
        best_substring_length_ratio = 0.0
        
        for ref_id, ref_note, norm_note, norm_id in rows:
            # First, check for word-level subset match
            words_note = set(norm_note.split())
            words_val = set(norm_val.split())
            if words_note and words_val and (words_note.issubset(words_val) or words_val.issubset(words_note)):
                ratio = min(len(words_note), len(words_val)) / max(len(words_note), len(words_val))
                if ratio > best_substring_length_ratio:
                    best_substring_length_ratio = ratio
                    best_substring_match = ref_id
            # Backup: character-level substring match
            elif (norm_note and norm_note in norm_val) or (norm_val and norm_val in norm_note):
                ratio = min(len(norm_note), len(norm_val)) / max(len(norm_note), len(norm_val))
                if ratio > best_substring_length_ratio:
                    best_substring_length_ratio = ratio
                    best_substring_match = ref_id

        # Lower threshold to 0.35 to support matching "Regular" (7 chars) in "regular employee" (16 chars)
        if best_substring_match and best_substring_length_ratio >= 0.35:
            print(f"[ReferenceResolver] Substring match found in {filename}: '{clean_val}' -> '{best_substring_match}' (ratio: {best_substring_length_ratio:.2f})", file=sys.stderr)
            return best_substring_match

        # 3. Third Pass: Fuzzy string matching using SequenceMatcher
        best_fuzzy_match = None
        best_score = 0.0
        
        for ref_id, ref_note, norm_note, norm_id in rows:
            # Compare note
            score = difflib.SequenceMatcher(None, norm_val, norm_note).ratio()
            if score > best_score:
                best_score = score
                best_fuzzy_match = ref_id
                
            # Compare id
            score_id = difflib.SequenceMatcher(None, norm_val, norm_id).ratio()
            if score_id > best_score:
                best_score = score_id
                best_fuzzy_match = ref_id

        # Set a conservative threshold of 0.75 for fuzzy matching
        if best_fuzzy_match and best_score >= 0.75:
            print(f"[ReferenceResolver] Fuzzy match found in {filename}: '{clean_val}' -> '{best_fuzzy_match}' (score: {best_score:.2f})", file=sys.stderr)
            return best_fuzzy_match

        print(f"[ReferenceResolver] No confident match for '{clean_val}' under parameter '{param_key}' in {filename}", file=sys.stderr)
        return None

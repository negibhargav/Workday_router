import unittest
import os
import sys

# Ensure project root is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.utils.reference_resolver import ReferenceResolver
from src.brain.executor import Executor

class TestReferenceResolver(unittest.TestCase):
    def setUp(self):
        self.resolver = ReferenceResolver()

    def test_exact_match(self):
        # Regular maps to Regular under employee_type_id
        resolved = self.resolver.resolve("employee_type_id", "Regular")
        self.assertEqual(resolved, "Regular")

        # San_Francisco_site maps to San_Francisco_site
        resolved = self.resolver.resolve("location_id", "San_Francisco_site")
        self.assertEqual(resolved, "San_Francisco_site")

    def test_case_insensitive_match(self):
        # san francisco maps to San_Francisco_site
        resolved = self.resolver.resolve("location_id", "san francisco")
        self.assertEqual(resolved, "San_Francisco_site")

    def test_substring_match(self):
        # San Francisco office maps to San_Francisco_site
        resolved = self.resolver.resolve("location_id", "San Francisco office")
        self.assertEqual(resolved, "San_Francisco_site")
        
        # regular employee maps to Regular
        resolved = self.resolver.resolve("employee_type_id", "regular employee")
        self.assertEqual(resolved, "Regular")

    def test_fuzzy_match(self):
        # San Fran (close to San Francisco) maps to San_Francisco_site
        resolved = self.resolver.resolve("location_id", "San Fran")
        self.assertEqual(resolved, "San_Francisco_site")

    def test_fallback_behavior(self):
        # Unknown should return None
        resolved = self.resolver.resolve("location_id", "Somewhere else entirely")
        self.assertIsNone(resolved)

    def test_dict_resolution(self):
        executor = Executor()
        d = {
            "location_id": "san francisco office",
            "employee_type_id": "regular worker",
            "unrelated_param": "some_value"
        }
        resolved_d = executor._resolve_references_in_dict(d)
        
        self.assertEqual(resolved_d["location_id"], "San_Francisco_site")
        self.assertEqual(resolved_d["employee_type_id"], "Regular")
        self.assertEqual(resolved_d["unrelated_param"], "some_value")

if __name__ == "__main__":
    unittest.main()

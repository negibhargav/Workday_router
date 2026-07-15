import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Ensure project root is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.services.hire import (
    HireSOAPService,
    get_zeep_client,
    build_hire_employee_request,
)
from zeep.exceptions import Fault

class TestHireSOAPService(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # We can initialize the real Zeep client since the WSDL URL is accessible
        try:
            cls.client = get_zeep_client()
        except Exception as e:
            print(f"Warning: Could not initialize real Zeep client for tests: {e}")
            cls.client = None

    def setUp(self):
        self.service = HireSOAPService()

    def test_required_fields_validation(self):
        # 1. Missing everything
        with self.assertRaises(ValueError) as ctx:
            self.service.hire_employee({})
        self.assertIn("missing required fields", str(ctx.exception))

        # 2. Missing worker choice
        with self.assertRaises(ValueError) as ctx:
            self.service.hire_employee({
                "position_id": "POS-1",
                "organization_id": "ORG-1",
                "hire_date": "2026-07-10"
            })
        self.assertIn("either (existing_worker_type + existing_worker_id) or (first_name + last_name)", str(ctx.exception))

        # 3. Missing position/requisition
        with self.assertRaises(ValueError) as ctx:
            self.service.hire_employee({
                "first_name": "John",
                "last_name": "Doe",
                "organization_id": "ORG-1",
                "hire_date": "2026-07-10"
            })
        self.assertIn("position_id or job_requisition_id", str(ctx.exception))

        # 4. Missing organization_id
        with self.assertRaises(ValueError) as ctx:
            self.service.hire_employee({
                "first_name": "John",
                "last_name": "Doe",
                "position_id": "POS-1",
                "hire_date": "2026-07-10"
            })
        self.assertIn("organization_id", str(ctx.exception))

        # 5. Missing hire_date
        with self.assertRaises(ValueError) as ctx:
            self.service.hire_employee({
                "first_name": "John",
                "last_name": "Doe",
                "position_id": "POS-1",
                "organization_id": "ORG-1"
            })
        self.assertIn("hire_date", str(ctx.exception))

    def test_build_request_new_worker(self):
        if not self.client:
            self.skipTest("Zeep client not initialized")

        args = {
            "first_name": "John",
            "last_name": "Doe",
            "middle_name": "Middle",
            "position_id": "POS-123",
            "organization_id": "ORG-456",
            "hire_date": "2026-07-10",
            "email_address": "john.doe@example.com",
            "phone_number": "1234567890",
            "address_line_1": "123 Main St",
            "address_city": "New York",
            "address_postal_code": "10001",
            "national_id": "999-99-9999",
            "national_id_type": "SSN",
            "national_id_country": "USA",
        }

        req = build_hire_employee_request(self.client, args)
        
        # Verify Hire_Employee_Data structure
        data = req["Hire_Employee_Data"]
        self.assertIsNotNone(data)
        
        # Verify Applicant_Data
        applicant_data = data.Applicant_Data
        self.assertIsNotNone(applicant_data)
        
        # Verify Legal_Name_Data
        legal_name = applicant_data.Personal_Data.Name_Data.Legal_Name_Data
        self.assertEqual(legal_name.Name_Detail_Data.First_Name, "John")
        self.assertEqual(legal_name.Name_Detail_Data.Last_Name, "Doe")
        self.assertEqual(legal_name.Name_Detail_Data.Middle_Name, "Middle")
        
        # Verify Contact_Data
        contact = applicant_data.Personal_Data.Contact_Data
        self.assertEqual(contact.Email_Address_Data[0].Email_Address, "john.doe@example.com")
        self.assertEqual(contact.Phone_Data[0].Phone_Number, "1234567890")
        self.assertEqual(contact.Address_Data[0].Address_Line_Data[0], "123 Main St")
        self.assertEqual(contact.Address_Data[0].Municipality, "New York")
        
        # Verify Identification_Data / National ID
        national_id_list = applicant_data.Personal_Data.Identification_Data.National_ID
        self.assertEqual(national_id_list[0].National_ID_Data.ID, "999-99-9999")

        # Verify Position_Reference and Organization_Reference
        self.assertEqual(data.Position_Reference.ID[0]._value_1, "POS-123")
        self.assertEqual(data.Organization_Reference.ID[0]._value_1, "ORG-456")
        self.assertEqual(str(data.Hire_Date), "2026-07-10")

    def test_build_request_existing_worker(self):
        if not self.client:
            self.skipTest("Zeep client not initialized")

        args = {
            "existing_worker_type": "applicant",
            "existing_worker_id": "APP-987",
            "job_requisition_id": "REQ-789",
            "organization_id": "ORG-456",
            "hire_date": "2026-07-10",
        }

        req = build_hire_employee_request(self.client, args)
        data = req["Hire_Employee_Data"]
        
        self.assertEqual(data.Applicant_Reference.ID[0]._value_1, "APP-987")
        self.assertEqual(data.Job_Requisition_Reference.ID[0]._value_1, "REQ-789")
        self.assertIsNone(getattr(data, "Applicant_Data", None))

    def test_position_details_creation_and_suppression(self):
        if not self.client:
            self.skipTest("Zeep client not initialized")

        args = {
            "first_name": "Jane",
            "last_name": "Smith",
            "position_id": "POS-111",
            "organization_id": "ORG-222",
            "hire_date": "2026-07-10",
            "job_profile_id": "JP-888",
            "position_title": "Software Engineer",
        }

        # Test case 1: Suppression toggle is False (default)
        with patch("src.services.hire._POSITION_ID_SUPPRESSES_DETAILS", False):
            req = build_hire_employee_request(self.client, args)
            event_data = req["Hire_Employee_Data"].Hire_Employee_Event_Data
            self.assertIsNotNone(event_data.Position_Details)
            self.assertEqual(event_data.Position_Details.Position_Title, "Software Engineer")
            self.assertEqual(event_data.Position_Details.Job_Profile_Reference.ID[0]._value_1, "JP-888")

        # Test case 2: Suppression toggle is True
        with patch("src.services.hire._POSITION_ID_SUPPRESSES_DETAILS", True):
            req = build_hire_employee_request(self.client, args)
            event_data = req["Hire_Employee_Data"].Hire_Employee_Event_Data
            self.assertIsNone(getattr(event_data, "Position_Details", None))

    def test_compensation_sub_process(self):
        if not self.client:
            self.skipTest("Zeep client not initialized")

        args = {
            "first_name": "Jane",
            "last_name": "Smith",
            "position_id": "POS-111",
            "organization_id": "ORG-222",
            "hire_date": "2026-07-10",
            "compensation_package_id": "PKG-1",
            "compensation_grade_id": "GRD-2",
            "base_pay_amount": 120000,
            "base_pay_currency_id": "USD",
            "base_pay_frequency_id": "Annual"
        }

        req = build_hire_employee_request(self.client, args)
        comp = req["Hire_Employee_Data"].Propose_Compensation_for_Hire_Sub_Process.Propose_Compensation_for_Employment_Data
        self.assertIsNotNone(comp)
        self.assertEqual(comp.Compensation_Guidelines_Data.Compensation_Package_Reference.ID[0]._value_1, "PKG-1")
        self.assertEqual(comp.Compensation_Guidelines_Data.Compensation_Grade_Reference.ID[0]._value_1, "GRD-2")
        self.assertEqual(comp.Pay_Plan_Data.Pay_Plan_Sub_Data[0].Amount, 120000)

    def test_advanced_fields_collision(self):
        if not self.client:
            self.skipTest("Zeep client not initialized")

        args = {
            "first_name": "Jane",
            "last_name": "Smith",
            "position_id": "POS-111",
            "organization_id": "ORG-222",
            "hire_date": "2026-07-10",
            "advanced_fields": {
                "Position_Reference": "some_value" # Collides with position_id mapping
            }
        }

        with self.assertRaises(ValueError) as ctx:
            build_hire_employee_request(self.client, args)
        self.assertIn("advanced_fields collides with fields already built", str(ctx.exception))

    @patch("src.services.hire.get_zeep_client")
    def test_hire_employee_soap_fault_handling(self, mock_get_client):
        # Mock Zeep service call to throw a Soap Fault
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        
        fault = Fault(message="Position is already occupied", detail="Detail elements")
        mock_client.service.Hire_Employee.side_effect = fault

        args = {
            "first_name": "Jane",
            "last_name": "Smith",
            "position_id": "POS-111",
            "organization_id": "ORG-222",
            "hire_date": "2026-07-10",
        }

        with self.assertRaises(RuntimeError) as ctx:
            self.service.hire_employee(args)
        self.assertIn("Workday rejected Hire_Employee — Position is already occupied | detail=Detail elements", str(ctx.exception))

    @patch("src.services.hire.get_zeep_client")
    def test_hire_employee_connection_error_handling(self, mock_get_client):
        # Mock Zeep service call to throw a network exception
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        
        mock_client.service.Hire_Employee.side_effect = Exception("Connection timed out")

        args = {
            "first_name": "Jane",
            "last_name": "Smith",
            "position_id": "POS-111",
            "organization_id": "ORG-222",
            "hire_date": "2026-07-10",
        }

        with self.assertRaises(RuntimeError) as ctx:
            self.service.hire_employee(args)
        self.assertIn("Hire_Employee call failed before/outside a SOAP fault", str(ctx.exception))

if __name__ == "__main__":
    unittest.main()

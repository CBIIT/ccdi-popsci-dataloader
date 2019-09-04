import unittest
from utils import *
import uuid

class TestUtils(unittest.TestCase):
    def test_uuid(self):
        self.assertEqual(uuid.UUID('f1b00b46-6079-5e20-b7f1-f3561f02d4e3'), get_uuid_for_node('case', 'abc'))
        self.assertEqual(uuid.UUID('9a17df30-4783-5f3f-bea3-9d530a7b3c57'), get_uuid_for_node('case', 'abcd'))
        self.assertEqual(uuid.UUID('3721b8bd-93bb-5460-9715-f7b61a8ec3ff'), get_uuid_for_node('study', 'abc'))
import unittest

class Smoke(unittest.TestCase):
    def test_python_runtime_and_project_fixture(self):
        self.assertEqual(sum((2, 3)), 5)

if __name__ == '__main__':
    unittest.main()

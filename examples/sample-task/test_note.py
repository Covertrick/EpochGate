import unittest

from note import greet


class GreetTest(unittest.TestCase):
    def test_greets(self) -> None:
        self.assertEqual(greet("ada"), "hello, ada")


if __name__ == "__main__":
    unittest.main()

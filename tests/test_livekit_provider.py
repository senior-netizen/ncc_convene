import unittest
from unittest.mock import MagicMock, patch
from app.conference import LiveKitProvider, ConferenceConfigurationError

class ProviderTests(unittest.TestCase):
    def test_missing_configuration_fails_closed(self):
        provider = LiveKitProvider("", "", "")
        with self.assertRaises(ConferenceConfigurationError):
            provider.participant_token("room", "identity", "Name", can_publish=True)

    @patch.dict("sys.modules", {})
    def test_configuration_flag_requires_all_secrets(self):
        self.assertFalse(LiveKitProvider("ws://localhost", "key", "").configured)
        self.assertTrue(LiveKitProvider("ws://localhost", "key", "secret").configured)

if __name__ == "__main__": unittest.main()

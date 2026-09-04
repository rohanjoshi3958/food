from unittest.mock import patch

from app.email import send_password_reset_email


class TestSendPasswordResetEmail:
    @patch("app.email.resend.Emails.send")
    @patch("app.email.settings")
    def test_sends_email_via_resend_when_api_key_set(self, mock_settings, mock_send):
        mock_settings.resend_api_key = "re_test_key"
        mock_settings.email_from = "Food <onboarding@resend.dev>"
        mock_settings.frontend_url = "http://localhost:3000/"
        mock_settings.password_reset_ttl_minutes = 30

        send_password_reset_email(to_email="user@example.com", reset_token="abc123")

        assert mock_settings.resend_api_key == "re_test_key"
        mock_send.assert_called_once()
        payload = mock_send.call_args[0][0]
        assert payload["from"] == "Food <onboarding@resend.dev>"
        assert payload["to"] == ["user@example.com"]
        assert payload["subject"] == "Reset your Food password"
        assert (
            'href="http://localhost:3000/reset-password?token=abc123"' in payload["html"]
        )
        assert "expires in 30 minutes" in payload["html"]

    @patch("app.email.resend.Emails.send")
    @patch("app.email.settings")
    def test_logs_reset_link_and_skips_send_when_api_key_missing(
        self, mock_settings, mock_send, caplog
    ):
        mock_settings.resend_api_key = ""
        mock_settings.frontend_url = "http://localhost:3000"
        mock_settings.password_reset_ttl_minutes = 30

        with caplog.at_level("WARNING", logger="app.email"):
            send_password_reset_email(to_email="user@example.com", reset_token="xyz")

        mock_send.assert_not_called()
        assert "RESEND_API_KEY not set" in caplog.text
        assert "http://localhost:3000/reset-password?token=xyz" in caplog.text

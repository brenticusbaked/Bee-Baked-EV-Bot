"""Tests for Supabase connection validation and db_manager robustness."""

from unittest.mock import MagicMock, patch

from db_manager import validate_supabase_connection, REQUIRED_TABLES


class TestValidateSupabaseConnection:
    def test_returns_not_ok_when_client_is_none(self):
        with patch("db_manager.supabase", None):
            result = validate_supabase_connection()
        assert result["ok"] is False
        assert result["connected"] is False
        assert len(result["errors"]) > 0

    def test_returns_connected_when_client_works(self):
        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.limit.return_value.execute.return_value = MagicMock(count=0)
        mock_client.table.return_value.select.return_value.count = "exact"
        with patch("db_manager.supabase", mock_client):
            result = validate_supabase_connection()
        assert result["connected"] is True
        assert result["ok"] is True
        assert len(result["errors"]) == 0

    def test_reports_missing_tables(self):
        mock_client = MagicMock()
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count > 3:
                raise Exception("relation does not exist")
            mock_result = MagicMock()
            mock_result.count = 0
            return mock_result

        mock_client.table.return_value.select.return_value.limit.return_value.execute = side_effect
        with patch("db_manager.supabase", mock_client):
            result = validate_supabase_connection()
        assert result["connected"] is True
        assert any("not accessible" in err for err in result["errors"])

    def test_required_tables_list_is_complete(self):
        expected = {
            "bets_log", "odds_cache", "bot_state", "alerts_sent",
            "workflow_runs", "execution_orders", "execution_child_orders",
            "execution_fills", "venue_metrics",
            "fixtures", "historical_odds",
        }
        assert set(REQUIRED_TABLES) == expected

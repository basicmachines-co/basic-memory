"""Tests for MCP server configuration."""

import os
import pytest
from unittest.mock import patch, MagicMock

from basic_memory.mcp.server import create_auth_config


class TestMCPServer:
    """Test MCP server configuration."""

    def test_create_auth_config_no_provider(self):
        """Test auth config creation when no provider is specified."""
        with patch.dict(os.environ, {}, clear=True):
            auth_settings, auth_provider = create_auth_config()
            assert auth_settings is None
            assert auth_provider is None

    def test_create_auth_config_github_provider(self):
        """Test auth config creation with GitHub provider."""
        env_vars = {"FASTMCP_AUTH_ENABLED": "true", "FASTMCP_AUTH_PROVIDER": "github"}
        with patch.dict(os.environ, env_vars):
            with patch("basic_memory.mcp.server.create_github_provider") as mock_create_github:
                mock_github_provider = MagicMock()
                mock_create_github.return_value = mock_github_provider

                auth_settings, auth_provider = create_auth_config()

                assert auth_settings is not None
                assert auth_provider == mock_github_provider
                mock_create_github.assert_called_once()

    def test_create_auth_config_google_provider(self):
        """Test auth config creation with Google provider."""
        env_vars = {"FASTMCP_AUTH_ENABLED": "true", "FASTMCP_AUTH_PROVIDER": "google"}
        with patch.dict(os.environ, env_vars):
            with patch("basic_memory.mcp.server.create_google_provider") as mock_create_google:
                mock_google_provider = MagicMock()
                mock_create_google.return_value = mock_google_provider

                auth_settings, auth_provider = create_auth_config()

                assert auth_settings is not None
                assert auth_provider == mock_google_provider
                mock_create_google.assert_called_once()

    def test_create_auth_config_supabase_provider_success(self):
        """Test auth config creation with Supabase provider (success case)."""
        env_vars = {
            "FASTMCP_AUTH_ENABLED": "true",
            "FASTMCP_AUTH_PROVIDER": "supabase",
            "SUPABASE_URL": "https://test.supabase.co",
            "SUPABASE_ANON_KEY": "anon-key-123",
            "SUPABASE_SERVICE_KEY": "service-key-456",
        }

        with patch.dict(os.environ, env_vars):
            with patch("basic_memory.mcp.server.SupabaseOAuthProvider") as mock_supabase_class:
                mock_supabase_provider = MagicMock()
                mock_supabase_class.return_value = mock_supabase_provider

                auth_settings, auth_provider = create_auth_config()

                assert auth_settings is not None
                assert auth_provider == mock_supabase_provider
                mock_supabase_class.assert_called_once_with(
                    supabase_url="https://test.supabase.co",
                    supabase_anon_key="anon-key-123",
                    supabase_service_key="service-key-456",
                    issuer_url="http://localhost:8000",  # Default issuer URL is added
                )

    def test_create_auth_config_supabase_provider_missing_url(self):
        """Test auth config creation with Supabase provider missing URL."""
        env_vars = {
            "FASTMCP_AUTH_ENABLED": "true",
            "FASTMCP_AUTH_PROVIDER": "supabase",
            "SUPABASE_ANON_KEY": "anon-key-123",
            # Missing SUPABASE_URL
        }

        with patch.dict(os.environ, env_vars):
            with pytest.raises(ValueError, match="SUPABASE_URL and SUPABASE_ANON_KEY must be set"):
                create_auth_config()

    def test_create_auth_config_supabase_provider_missing_anon_key(self):
        """Test auth config creation with Supabase provider missing anon key."""
        env_vars = {
            "FASTMCP_AUTH_ENABLED": "true",
            "FASTMCP_AUTH_PROVIDER": "supabase",
            "SUPABASE_URL": "https://test.supabase.co",
            # Missing SUPABASE_ANON_KEY
        }

        with patch.dict(os.environ, env_vars):
            with pytest.raises(ValueError, match="SUPABASE_URL and SUPABASE_ANON_KEY must be set"):
                create_auth_config()

    def test_create_auth_config_basic_memory_provider(self):
        """Test auth config creation with basic-memory provider."""
        env_vars = {
            "FASTMCP_AUTH_ENABLED": "true",
            "FASTMCP_AUTH_PROVIDER": "basic-memory",
            "FASTMCP_AUTH_SECRET_KEY": "test-secret-key",
            "FASTMCP_AUTH_ISSUER_URL": "https://custom-issuer.com",
        }

        with patch.dict(os.environ, env_vars):
            with patch(
                "basic_memory.mcp.server.BasicMemoryOAuthProvider"
            ) as mock_basic_memory_class:
                mock_basic_memory_provider = MagicMock()
                mock_basic_memory_class.return_value = mock_basic_memory_provider

                auth_settings, auth_provider = create_auth_config()

                assert auth_settings is not None
                assert auth_provider == mock_basic_memory_provider
                mock_basic_memory_class.assert_called_once_with(
                    issuer_url="https://custom-issuer.com"
                )

    def test_create_auth_config_basic_memory_provider_default_issuer(self):
        """Test auth config creation with basic-memory provider using default issuer."""
        env_vars = {
            "FASTMCP_AUTH_ENABLED": "true",
            "FASTMCP_AUTH_PROVIDER": "basic-memory",
            "FASTMCP_AUTH_SECRET_KEY": "test-secret-key",
            # No FASTMCP_AUTH_ISSUER_URL - should use default
        }

        with patch.dict(os.environ, env_vars):
            with patch(
                "basic_memory.mcp.server.BasicMemoryOAuthProvider"
            ) as mock_basic_memory_class:
                mock_basic_memory_provider = MagicMock()
                mock_basic_memory_class.return_value = mock_basic_memory_provider

                auth_settings, auth_provider = create_auth_config()

                assert auth_settings is not None
                assert auth_provider == mock_basic_memory_provider
                mock_basic_memory_class.assert_called_once_with(issuer_url="http://localhost:8000")

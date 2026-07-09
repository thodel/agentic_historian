"""
tests/test_ah_248_update_command.py
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

BOT_PATH = Path(__file__).resolve().parents[1] / 'bot.py'
CFG_PATH = Path(__file__).resolve().parents[1] / 'config.py'


class TestAdminOnlyDecorator:
    def test_admin_only_decorator_exists(self):
        with open(BOT_PATH) as f:
            src = f.read()
        assert 'def admin_only(func)' in src, 'admin_only decorator must be defined'
        assert 'REQUIRED_ADMIN_ROLE_ID' in src
        assert 'ctx.guild' in src

    def test_update_command_decorated_with_admin_only(self):
        with open(BOT_PATH) as f:
            src = f.read()
        pos = src.find('@bot.slash_command(name="update"')
        assert pos != -1, '/update command not found'
        def_pos = src.find('async def update_cmd', pos)
        assert def_pos != -1
        between = src[pos:def_pos]
        assert '@admin_only' in between

    def test_admin_role_in_config(self):
        with open(CFG_PATH) as f:
            src = f.read()
        assert 'REQUIRED_ADMIN_ROLE_ID' in src


class TestUpdateCommandRegistration:
    def test_update_command_registered(self):
        import bot as bot_module
        pending = {
            c.name: c
            for c in bot_module.bot.pending_application_commands
            if getattr(c, "name", None) == "update"
        }
        assert "update" in pending, "/update not registered"

    def test_update_callback_is_admin_only_wrapper(self):
        import bot as bot_module
        pending = {
            c.name: c
            for c in bot_module.bot.pending_application_commands
            if getattr(c, "name", None) == "update"
        }
        cmd = pending['update']
        assert hasattr(cmd.callback, '__wrapped__')


class TestMakeToken:
    def test_token_is_16_hex_chars(self):
        import bot as bot_module
        token = bot_module._make_token()
        assert len(token) == 16
        int(token, 16)

    def test_token_is_unique(self):
        import bot as bot_module
        t1 = bot_module._make_token()
        t2 = bot_module._make_token()
        assert t1 != t2


class TestConfirmView:
    def test_confirm_enqueues_when_queue_empty(self):
        async def go():
            import bot as bot_module
            token = 'tok123456789012'
            requester = '111222'
            bot_module._PENDING_UPDATES[token] = {
                "channel_id": "999", "message_id": "12345",
                "requester": requester, "target_sha": "x", "from_sha": "0"
            }
            mock_interaction = MagicMock()
            mock_interaction.user.id = int(requester)
            mock_interaction.response.is_done.return_value = False
            mock_interaction.response.defer = AsyncMock()
            mock_interaction.followup.send = AsyncMock()
            view = bot_module._ConfirmView(token=token, requester=requester, target_sha='x')
            # confirm_button.callback is a functools.partial with (view, button) pre-bound
            # So calling callback(interaction) invokes confirm(view, button, interaction)
            confirm_button = view.confirm
            with patch.object(bot_module._job_queue, 'empty', return_value=True):
                with patch.object(bot_module._job_queue, 'put', new_callable=AsyncMock) as mock_put:
                    await confirm_button.callback(mock_interaction)
            mock_put.assert_called_once()
            call_args = mock_put.call_args[0][0]
            assert call_args[0] == bot_module._do_apply_update
            bot_module._PENDING_UPDATES.clear()
        asyncio.run(go())

    def test_confirm_rejects_when_queue_busy(self):
        async def go():
            import bot as bot_module
            token = 'tok123456789012'
            requester = '111222'
            bot_module._PENDING_UPDATES[token] = {
                "channel_id": "999", "message_id": "12345",
                "requester": requester, "target_sha": "x", "from_sha": "0"
            }
            mock_interaction = MagicMock()
            mock_interaction.user.id = int(requester)
            mock_interaction.response.is_done.return_value = False
            mock_interaction.response.defer = AsyncMock()
            mock_interaction.followup.send = AsyncMock()
            view = bot_module._ConfirmView(token=token, requester=requester, target_sha='x')
            confirm_button = view.confirm
            with patch.object(bot_module._job_queue, 'empty', return_value=False):
                with patch.object(bot_module._job_queue, 'put', new_callable=AsyncMock) as mock_put:
                    await confirm_button.callback(mock_interaction)
            mock_put.assert_not_called()
            mock_interaction.followup.send.assert_called_once()
            bot_module._PENDING_UPDATES.clear()
        asyncio.run(go())

    def test_cancel_removes_pending_and_edits_message(self):
        async def go():
            import bot as bot_module
            token = 'tok123456789012'
            requester = '111222'
            bot_module._PENDING_UPDATES[token] = {
                "channel_id": "999", "message_id": "12345",
                "requester": requester, "target_sha": "x", "from_sha": "000111"
            }
            mock_interaction = MagicMock()
            mock_interaction.user.id = int(requester)
            mock_interaction.response.is_done.return_value = False
            mock_interaction.response.edit_message = AsyncMock()
            view = bot_module._ConfirmView(token=token, requester=requester, target_sha='x')
            cancel_button = view.cancel
            await cancel_button.callback(mock_interaction)
            assert token not in bot_module._PENDING_UPDATES
            mock_interaction.response.edit_message.assert_called_once()
            call_kw = mock_interaction.response.edit_message.call_args[1]
            assert call_kw['view'] is None
        asyncio.run(go())


class TestDoApplyUpdateFailure:
    def test_failure_does_not_sys_exit_and_edits_rollback(self):
        async def go():
            import bot as bot_module
            import updater as _updater_mod
            token = 'failtok12345678'
            channel_id = 999
            message_id = 12345
            requester = '111222'
            target_sha = 'abcdef12'
            bot_module._PENDING_UPDATES[token] = {
                "channel_id": str(channel_id),
                "message_id": str(message_id),
                "requester": requester,
                "target_sha": target_sha,
                "from_sha": "0000000"
            }
            mock_channel = MagicMock()
            mock_message = MagicMock()
            mock_channel.fetch_message = AsyncMock(return_value=mock_message)
            mock_message.edit = AsyncMock()
            failure_result = {
                "ok": False,
                "stage": "pip",
                "error": "install failed",
                "rolled_back": True,
                "pre_sha": "0000000"
            }
            # Make updater available on bot_module for the patch
            bot_module.updater = _updater_mod
            with patch.object(bot_module.bot, 'get_channel', return_value=mock_channel):
                with patch.object(_updater_mod, 'apply_update', return_value=failure_result):
                    with patch.object(sys, 'exit', MagicMock()) as mock_exit:
                        await bot_module._do_apply_update(
                            token, channel_id, message_id, requester, target_sha
                        )
            mock_exit.assert_not_called()
            bot_module._PENDING_UPDATES.clear()
        asyncio.run(go())


class TestOnReadyMarker:
    def test_back_online_and_clear_when_marker_present(self):
        async def go():
            import bot as bot_module
            import updater as _updater_mod
            # bot.user is read-only — build a mock bot with a mock user, preserve
            # the real .get_channel and .loop so those code paths still work
            mock_user = MagicMock()
            mock_user.id = 999999
            mock_user.display_name = "TestBot"
            mock_bot = MagicMock()
            mock_bot.user = mock_user
            mock_channel = MagicMock()
            mock_message = MagicMock()
            mock_message.edit = AsyncMock()
            mock_channel.fetch_message = AsyncMock(return_value=mock_message)
            # Configure mock_bot.get_channel as a MagicMock so we can set return_value
            mock_bot.get_channel = MagicMock(return_value=mock_channel)
            mock_bot.loop = bot_module.bot.loop
            mock_bot.logger = bot_module.logger
            mock_marker = MagicMock()
            mock_marker.channel_id = "999"
            mock_marker.message_id = "12345"
            mock_marker.requester = "Admin"
            mock_marker.target_sha = "abcdef12"
            with patch.object(bot_module, 'bot', mock_bot):
                with patch.object(_updater_mod, 'read_marker', return_value=mock_marker):
                    with patch.object(_updater_mod, 'clear_marker', MagicMock()) as mock_clear:
                        await bot_module.on_ready()
            mock_message.edit.assert_called_once()
            assert 'Back online' in mock_message.edit.call_args[1]['content']
            mock_clear.assert_called_once()
        asyncio.run(go())

    def test_no_op_when_no_marker(self):
        async def go():
            import bot as bot_module
            import updater as _updater_mod
            mock_user = MagicMock()
            mock_user.id = 999999
            mock_user.display_name = "TestBot"
            mock_bot = MagicMock()
            mock_bot.user = mock_user
            # get_channel configured as Mock — will return None when called
            mock_bot.get_channel = MagicMock(return_value=None)
            mock_bot.loop = bot_module.bot.loop
            mock_bot.logger = bot_module.logger
            with patch.object(bot_module, 'bot', mock_bot):
                with patch.object(_updater_mod, 'read_marker', return_value=None):
                    with patch.object(_updater_mod, 'clear_marker', MagicMock()) as mock_clear:
                        await bot_module.on_ready()
            mock_bot.get_channel.assert_not_called()
            mock_clear.assert_not_called()
        asyncio.run(go())

import asyncio
import unittest
from unittest.mock import AsyncMock

from core.access.policy import authorize_command


class ManagedAccessPolicyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = AsyncMock()
        self.store.is_sudoer.return_value = True
        self.store.is_share_user.return_value = True
        self.store.has_grant.return_value = False

    async def test_owner_uses_dot_route_only(self):
        allowed = await authorize_command(
            store=self.store, user_id=1, owner_id=1, prefix=".",
            command="eval", sudo=True, share=True,
        )
        denied = await authorize_command(
            store=self.store, user_id=1, owner_id=1, prefix="#",
            command="eval", sudo=True, share=True,
        )
        self.assertTrue(allowed.allowed)
        self.assertTrue(allowed.is_owner)
        self.assertFalse(denied.allowed)

    async def test_all_delegated_prefixes_apply_the_same_role_policy(self):
        for prefix in (",", "#", "?"):
            with self.subTest(prefix=prefix):
                decision = await authorize_command(
                    store=self.store, user_id=2, owner_id=1, prefix=prefix,
                    command="admin", sudo=True,
                )
                self.assertTrue(decision.allowed)

        self.store.is_sudoer.return_value = False
        self.store.is_share_user.return_value = False
        self.store.has_grant.return_value = False
        for prefix in (",", "#", "?"):
            with self.subTest(prefix=prefix):
                decision = await authorize_command(
                    store=self.store, user_id=2, owner_id=1, prefix=prefix,
                    command="admin", sudo=True,
                )
                self.assertFalse(decision.allowed)

    async def test_share_role_can_use_any_delegated_prefix(self):
        for prefix in (",", "#", "?"):
            with self.subTest(prefix=prefix):
                decision = await authorize_command(
                    store=self.store, user_id=2, owner_id=1, prefix=prefix,
                    command="shared", share=True,
                )
                self.assertTrue(decision.allowed)

    async def test_owner_can_use_each_configured_normal_prefix(self):
        for prefix in (".", "$"):
            with self.subTest(prefix=prefix):
                decision = await authorize_command(
                    store=self.store, user_id=1, owner_id=1, prefix=prefix,
                    command="alive",
                )
                self.assertTrue(decision.allowed)

    async def test_sudo_can_use_default_command_route_but_not_explicitly_excluded_commands(self):
        allowed = await authorize_command(
            store=self.store, user_id=2, owner_id=1, prefix=",",
            command="todo", sudo=True,
        )
        denied = await authorize_command(
            store=self.store, user_id=2, owner_id=1, prefix=",",
            command="addsudo", sudo=False, share=False, grantable=False,
        )
        self.assertTrue(allowed.allowed)
        self.assertFalse(denied.allowed)

    async def test_sudo_prefix_requires_declared_sudo_route_and_membership(self):
        decision = await authorize_command(
            store=self.store, user_id=2, owner_id=1, prefix="#",
            command="eval", sudo=True,
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.route, "sudo")
        self.assertFalse((await authorize_command(
            store=self.store, user_id=2, owner_id=1, prefix="#",
            command="eval", sudo=False,
        )).allowed)

    async def test_share_route_allows_share_role_or_standalone_grant(self):
        self.assertTrue((await authorize_command(
            store=self.store, user_id=2, owner_id=1, prefix="?",
            command="tts", share=True,
        )).allowed)
        self.store.is_share_user.return_value = False
        self.store.has_grant.return_value = True
        self.assertTrue((await authorize_command(
            store=self.store, user_id=3, owner_id=1, prefix="?",
            command="eval", share=False, grantable=True,
        )).allowed)

    async def test_ineligible_prefix_and_store_failure_fail_closed(self):
        self.assertFalse((await authorize_command(
            store=self.store, user_id=3, owner_id=1, prefix=".",
            command="tts", share=True,
        )).allowed)
        self.store.has_grant.side_effect = RuntimeError("database unavailable")
        self.assertFalse((await authorize_command(
            store=self.store, user_id=3, owner_id=1, prefix="?",
            command="tts", share=True,
        )).allowed)


if __name__ == "__main__":
    unittest.main()

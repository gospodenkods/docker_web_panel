import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("PANEL_USER", "admin")
os.environ.setdefault("PANEL_PASSWORD", "test-password")
os.environ.setdefault("JWT_SECRET", "test-secret-at-least-long-enough")

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from app import main


class CoreTests(unittest.TestCase):
    def test_self_check_uses_isolated_compose_project(self):
        script = (
            Path(__file__).resolve().parent.parent / "scripts" / "self-check.sh"
        ).read_text(encoding="utf-8")
        self.assertIn('project="dockpilot_selfcheck_$$"', script)
        self.assertIn("PANEL_PORT=0", script)
        self.assertIn('--env-file "$env_file" -p "$project"', script)
        self.assertIn("down --rmi local -v --remove-orphans", script)
        self.assertNotIn("docker compose down -v", script)

    def test_token_roundtrip(self):
        token = main.make_token("admin")
        creds = MagicMock(credentials=token)
        self.assertEqual(main.verify_token(creds), "admin")

    def test_login_success_and_failure(self):
        request = MagicMock(headers={}, client=MagicMock(host="127.0.0.1"))
        main._login_attempts.clear()
        result = main.login(main.LoginIn(username="admin", password="test-password"), request)
        self.assertIn("access_token", result)
        with self.assertRaises(main.HTTPException) as ctx:
            main.login(main.LoginIn(username="admin", password="wrong"), request)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_login_rate_limit(self):
        request = MagicMock(headers={}, client=MagicMock(host="10.0.0.5"))
        main._login_attempts.clear()
        with patch.object(main, "LOGIN_MAX_ATTEMPTS", 2):
            for _ in range(2):
                with self.assertRaises(main.HTTPException) as ctx:
                    main.login(main.LoginIn(username="admin", password="wrong"), request)
                self.assertEqual(ctx.exception.status_code, 401)
            with self.assertRaises(main.HTTPException) as ctx:
                main.login(main.LoginIn(username="admin", password="wrong"), request)
            self.assertEqual(ctx.exception.status_code, 429)

    @patch("app.main.client")
    def test_redis_quick_deploy_requires_password(self, mocked_client):
        c = mocked_client.return_value
        c.images.get.return_value = MagicMock()
        c.containers.run.return_value = MagicMock(short_id="r1", name="redis")
        result = main.quick_deploy(main.QuickDeploy(template="redis", name="redis"), "admin")
        password = result["generated_credentials"]["password"]
        self.assertEqual(c.containers.run.call_args.kwargs["command"], ["redis-server", "--requirepass", password])

    @patch("app.main.client")
    def test_health_success(self, mocked_client):
        mocked_client.return_value.ping.return_value = True
        self.assertEqual(main.health(), {"ok": True, "docker": True})

    @patch("app.main.client")
    def test_dashboard(self, mocked_client):
        c = mocked_client.return_value
        c.info.return_value = {"Name":"host","ServerVersion":"1","OperatingSystem":"Linux","KernelVersion":"k","NCPU":2,"MemTotal":1024}
        running = MagicMock(status="running")
        stopped = MagicMock(status="exited")
        c.containers.list.return_value = [running, stopped]
        c.images.list.return_value = [MagicMock()]
        c.networks.list.return_value = [MagicMock(), MagicMock()]
        result = main.dashboard("admin")
        self.assertEqual(result["counts"], {"containers":2,"running":1,"stopped":1,"images":1,"networks":2})

    @patch("app.main.shutil.disk_usage")
    @patch("app.main.client")
    @patch("app.main.psutil.cpu_percent")
    def test_metrics(self, mocked_cpu, mocked_client, mocked_disk):
        mocked_cpu.return_value = 12.5
        mocked_disk.return_value = MagicMock(total=1000, used=400, free=600)
        mocked_client.return_value.containers.list.return_value = [MagicMock(id="one")]
        mocked_client.return_value.api.stats.return_value = {
            "networks": {
                "eth0": {"tx_bytes": 200, "rx_bytes": 300},
            }
        }
        result = main.metrics("admin")
        self.assertEqual(result["cpu_percent"], 12.5)
        self.assertEqual(result["network"], {"bytes_sent": 200, "bytes_recv": 300})
        self.assertEqual(result["disk"]["percent"], 40.0)

    @patch("app.main.client")
    def test_container_environment(self, mocked_client):
        obj = mocked_client.return_value.containers.get.return_value
        obj.name = "/web"
        obj.attrs = {"Config": {"Env": ["TOKEN=secret", "EMPTY=", "FLAG"]}}
        result = main.container_environment("abc", "admin")
        self.assertEqual(result["container"], "web")
        self.assertEqual(
            result["variables"],
            [
                {"key": "EMPTY", "value": ""},
                {"key": "FLAG", "value": ""},
                {"key": "TOKEN", "value": "secret"},
            ],
        )

    @patch("app.main.client")
    def test_create_container_pulls_missing_image(self, mocked_client):
        c = mocked_client.return_value
        c.images.get.side_effect = main.ImageNotFound("missing")
        obj = MagicMock(short_id="abc123", name="web")
        c.containers.run.return_value = obj
        data = main.ContainerCreate(image="nginx:alpine", name="web", ports={"80/tcp":8080})
        result = main.create_container(data, "admin")
        c.images.pull.assert_called_once_with("nginx:alpine")
        self.assertTrue(result["ok"])

    def test_restart_policy_validation(self):
        with self.assertRaises(Exception):
            main.ContainerCreate(image="nginx", name="x", restart_policy="invalid")


    def test_auto_remove_rejects_restart_policy(self):
        with self.assertRaises(Exception):
            main.ContainerCreate(
                image="nginx", name="temp", auto_remove=True, restart_policy="unless-stopped"
            )

    def test_network_driver_rejects_unsupported_modes(self):
        with self.assertRaises(Exception):
            main.NetworkCreate(name="testnet", driver="overlay")

    def test_api_error_preserves_http_exception(self):
        original = main.HTTPException(409, "conflict")
        with self.assertRaises(main.HTTPException) as ctx:
            main.api_error(original)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_api_error_maps_docker_failure_to_503(self):
        with self.assertRaises(main.HTTPException) as ctx:
            main.api_error(main.DockerException("daemon unavailable"))
        self.assertEqual(ctx.exception.status_code, 503)

    @patch("app.main.client")
    def test_stats_rejects_stopped_container(self, mocked_client):
        obj = mocked_client.return_value.containers.get.return_value
        obj.status = "exited"
        with self.assertRaises(main.HTTPException) as ctx:
            main.container_stats("abc", "admin")
        self.assertEqual(ctx.exception.status_code, 409)

    @patch("app.main.client")
    def test_quick_deploy_generates_database_password(self, mocked_client):
        c = mocked_client.return_value
        c.images.get.return_value = MagicMock()
        c.containers.run.return_value = MagicMock(short_id="db1", name="db")
        result = main.quick_deploy(main.QuickDeploy(template="postgres", name="db"), "admin")
        env = c.containers.run.call_args.kwargs["environment"]
        self.assertIn("POSTGRES_PASSWORD", env)
        self.assertNotIn("change", env["POSTGRES_PASSWORD"].lower())
        self.assertEqual(result["generated_credentials"]["password"], env["POSTGRES_PASSWORD"])

    def test_quick_deploy_name_validation(self):
        with self.assertRaises(Exception):
            main.QuickDeploy(template="postgres", name="bad name")

    def test_builtin_templates_bind_to_loopback(self):
        for cfg in main.TEMPLATES.values():
            for binding in (cfg.get("ports") or {}).values():
                self.assertIsInstance(binding, tuple)
                self.assertEqual(binding[0], "127.0.0.1")


if __name__ == "__main__":
    unittest.main(verbosity=2)

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("PANEL_USER", "admin")
os.environ.setdefault("PANEL_PASSWORD", "test-password")
os.environ.setdefault("JWT_SECRET", "test-secret-at-least-long-enough")

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from app import main
from app import backups


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

    @patch("app.main.client")
    def test_networks_normalize_null_ipam_config(self, mocked_client):
        network = MagicMock(short_id="net1", name="bridge")
        network.attrs = {
            "Driver": "bridge",
            "Scope": "local",
            "Internal": False,
            "Attachable": False,
            "IPAM": {"Config": None},
            "Containers": None,
        }
        mocked_client.return_value.networks.list.return_value = [network]
        result = main.networks("admin")
        self.assertEqual(result[0]["subnets"], [])
        self.assertEqual(result[0]["containers"], [])

    @patch("app.main.client")
    def test_update_network_rejects_attached_containers(self, mocked_client):
        network = mocked_client.return_value.networks.get.return_value
        network.name = "app-net"
        network.attrs = {"Containers": {"container-id": {"Name": "web"}}}
        data = main.NetworkUpdate(name="app-net", subnet="172.30.0.0/16")
        with self.assertRaises(main.HTTPException) as ctx:
            main.update_network("net1", data, "admin")
        self.assertEqual(ctx.exception.status_code, 409)
        network.remove.assert_not_called()

    @patch("app.main.client")
    def test_update_network_recreates_unused_network(self, mocked_client):
        c = mocked_client.return_value
        network = c.networks.get.return_value
        network.name = "app-net"
        network.attrs = {
            "Containers": {},
            "Labels": {"project": "app"},
            "Options": {},
            "EnableIPv6": False,
            "Driver": "bridge",
            "Internal": False,
            "Attachable": True,
            "IPAM": {"Config": [{"Subnet": "172.30.0.0/16", "Gateway": "172.30.0.1"}]},
        }
        recreated = MagicMock(short_id="new1")
        recreated.name = "app-net-new"
        c.networks.create.return_value = recreated
        data = main.NetworkUpdate(
            name="app-net-new",
            subnet="172.31.0.0/16",
            gateway="172.31.0.1",
            attachable=True,
        )
        result = main.update_network("net1", data, "admin")
        network.remove.assert_called_once()
        self.assertEqual(result, {"ok": True, "id": "new1", "name": "app-net-new"})
        self.assertEqual(c.networks.create.call_args.args[0], "app-net-new")

    def test_network_gateway_requires_subnet(self):
        with self.assertRaises(Exception):
            main.NetworkUpdate(name="app-net", gateway="172.30.0.1")

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

    def test_backup_settings_hide_webdav_password(self):
        with tempfile.TemporaryDirectory() as directory:
            settings_file = Path(directory) / "settings.json"
            with patch.object(backups, "SETTINGS_FILE", settings_file):
                backups.save_settings({
                    "enabled": True,
                    "interval_hours": 12,
                    "target": "webdav",
                    "webdav_url": "https://cloud.example.test/dav",
                    "webdav_username": "admin",
                    "webdav_password": "secret",
                    "webdav_path": "dockpilot",
                })
                public = backups.load_settings()
                private = backups.load_settings(include_password=True)
                self.assertNotIn("webdav_password", public)
                self.assertTrue(public["webdav_password_set"])
                self.assertEqual(private["webdav_password"], "secret")

    def test_backup_rejects_parent_path(self):
        with self.assertRaises(ValueError):
            backups._safe_relative("../outside")

    def test_backup_creates_tar_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            container = MagicMock()
            container.id = "container-full-id"
            container.short_id = "abc123"
            container.name = "web"
            container.attrs = {
                "Config": {"Image": "nginx:alpine", "Env": []},
                "HostConfig": {},
                "Mounts": [],
            }
            image = MagicMock()
            image.id = "image-id"
            image.tags = ["dockpilot-backup/web:test"]
            image.save.return_value = iter([b"docker-", b"archive"])
            container.commit.return_value = image
            docker_client = MagicMock()
            docker_client.containers.list.return_value = [container]
            with (
                patch.object(backups, "BACKUP_DIR", root / "backups"),
                patch.object(backups, "SETTINGS_FILE", root / "settings.json"),
                patch.object(backups, "HISTORY_FILE", root / "history.json"),
            ):
                result = backups.run_backup(docker_client, ["abc123"])
                self.assertEqual(result["status"], "completed")
                self.assertEqual(len(list((root / "backups").glob("*.tar"))), 1)
                self.assertEqual(len(list((root / "backups").glob("*.json"))), 1)
                container.commit.assert_called_once()
                docker_client.images.remove.assert_called_once_with("image-id", force=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)

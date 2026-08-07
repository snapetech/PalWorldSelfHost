import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class MaintenanceTests(unittest.TestCase):
    def test_restart_trap_is_armed_before_shutdown(self):
        script = (ROOT / "scripts" / "maintenance.sh").read_text()
        trap = "trap 'systemctl start palworld.service' EXIT"
        shutdown = '"$SCRIPT_DIR/graceful-shutdown.sh" "$wait_seconds"'
        self.assertLess(script.index(trap), script.index(shutdown))

    def test_unit_does_not_require_service_it_stops(self):
        unit = (ROOT / "systemd" / "palworld-maintenance.service").read_text()
        self.assertNotIn("Requires=palworld.service", unit)

    def test_immediate_shutdown_uses_stop_endpoint(self):
        script = (ROOT / "scripts" / "graceful-shutdown.sh").read_text()
        self.assertIn("if (( wait_seconds > 0 )); then", script)
        self.assertIn('"$SCRIPT_DIR/rest-client.py" stop', script)

    def test_systemd_stop_flushes_and_stops_before_signal_fallback(self):
        unit = (ROOT / "systemd/palworld.service").read_text()
        adapter = (ROOT / "scripts/service-stop.sh").read_text()
        self.assertIn("ExecStop=/usr/local/lib/palworld/service-stop.sh", unit)
        self.assertLess(adapter.index('rest-client.py" save'), adapter.index('rest-client.py" stop'))

    def test_root_runs_steamcmd_as_service_account(self):
        script = (ROOT / "scripts" / "update.sh").read_text()
        self.assertIn('runuser -u "$PALWORLD_USER"', script)

    def test_update_refuses_to_mutate_executables_after_backup_failure(self):
        script = (ROOT / "scripts" / "maintenance.sh").read_text()
        backup_gate = "if (( backup_rc == 0 )); then"
        update = '"$SCRIPT_DIR/update.sh" || update_rc=$?'
        self.assertLess(script.index(backup_gate), script.index(update))
        self.assertIn('ops_event audit update skipped --details "verified backup failed', script)

    def test_post_update_health_failure_restores_and_pins_previous_build(self):
        script = (ROOT / "scripts" / "maintenance.sh").read_text()
        self.assertIn('restore "$previous_build" --confirm "ROLLBACK BUILD"', script)
        self.assertIn('pin "$previous_build"', script)
        self.assertIn("health_rc != 0", script)

    def test_update_script_snapshots_and_restores_on_steam_or_render_failure(self):
        script = (ROOT / "scripts" / "update.sh").read_text()
        self.assertIn('steam-build-manager.py" snapshot', script)
        self.assertIn('restore "$snapshot_build" --confirm "ROLLBACK BUILD"', script)

    def test_public_address_is_explicitly_advertised(self):
        start = (ROOT / "scripts" / "start.sh").read_text()
        render = (ROOT / "scripts" / "render-settings.py").read_text()
        self.assertIn('-publicport="$PALWORLD_PORT"', start)
        self.assertIn('-publicip="$PALWORLD_PUBLIC_IP"', start)
        self.assertIn('-ip="$PALWORLD_BIND_IP"', start)
        self.assertIn('replace(text, "PublicIP"', render)


if __name__ == "__main__":
    unittest.main()

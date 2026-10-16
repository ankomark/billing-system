import { renderWithProviders, screen, fireEvent } from "../test-utils";
import Overview from "../components/admin/fiber/Overview";
import { dbm, metres, minutes } from "../components/admin/fiber/shared";

const port = (n, extra = {}) => ({
  pon_port: n, total: 6, online: 6, offline: 0, unknown_status: 0, good: 5, warning: 1, critical: 0,
  overload: 0, power_unknown: 0, unlinked: 2, new: 0, open_outages: 0, peak_mbps: 120.5,
  utilisation_pct: 12.1, ...extra,
});

const data = {
  as_of: "2026-10-16T10:00:00Z",
  totals: { total: 12, online: 9, offline: 3, warning: 1, critical: 1 },
  open_outages: 1, open_tickets: 2, degrading: 1,
  olts: [{
    id: 1, name: "VSOL Makongeni", vendor: "VSOL", model: "V1600D-MINI", mgmt_ip: "10.0.0.2",
    reachable: true, last_polled_at: "2026-10-16T09:58:00Z", router: "skylink3", uplink: "sfp-sfpplus3",
    ports: [port(1), port(2, { offline: 3, online: 3, open_outages: 1 })],
  }],
  worst_signal: [{ id: 7, label: "Mama Njeri", olt: "VSOL Makongeni", position: "PON1:7",
                   rx_power_dbm: -27.6, band: "critical", distance_m: 3410 }],
};

describe("Fiber overview", () => {
  it("shows each port and flags the one in an outage", () => {
    const onPort = jest.fn();
    renderWithProviders(<Overview data={data} onPort={onPort} />);
    expect(screen.getByText("VSOL Makongeni")).toBeInTheDocument();
    expect(screen.getByText("Outage in progress")).toBeInTheDocument();
    fireEvent.click(screen.getByText("PON 2"));
    expect(onPort).toHaveBeenCalledWith(1, 2);
    expect(screen.getByText("Mama Njeri")).toBeInTheDocument();
    expect(screen.getByText("-27.6 dBm")).toBeInTheDocument();
  });

  it("explains what to do when no OLT is registered", () => {
    renderWithProviders(<Overview data={{ ...data, olts: [] }} onPort={() => {}} />);
    expect(screen.getByText(/No OLTs registered yet/)).toBeInTheDocument();
  });

  it("formats units the way a technician reads them", () => {
    expect(dbm(null)).toBe("—");
    expect(dbm(-21.456)).toBe("-21.5 dBm");
    expect(metres(850)).toBe("850 m");
    expect(metres(3410)).toBe("3.41 km");
    expect(minutes(135)).toBe("2 h 15 m");
  });
});

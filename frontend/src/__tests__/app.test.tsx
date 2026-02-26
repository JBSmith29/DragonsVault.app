import { render, screen } from "@testing-library/react";
import { vi } from "vitest";

import App from "../App";
import * as endpoints from "../api/endpoints";

vi.mock("../api/endpoints", () => ({
  pingWebApi: vi.fn(async () => ({ status: "ok", service: "web" })),
  pingUserManager: vi.fn(async () => ({ status: "ok", service: "user" })),
  pingCardData: vi.fn(async () => ({ status: "ok", service: "card" })),
  pingFolderService: vi.fn(async () => ({ status: "ok", service: "folders" })),
  pingPriceService: vi.fn(async () => ({ status: "ok", service: "prices" })),
  fetchMaintenanceStatus: vi.fn(async () => ({
    scryfall: { status: "ok", updated_at: null },
    spellbook: { status: "ok", updated_at: null },
    fts: { status: "ok", updated_at: null },
    edhrec: { status: "ok", updated_at: null },
  })),
  fetchCardDataSyncStatus: vi.fn(async () => ({
    status: "ok",
    updated_at: null,
    processed_at: null,
    record_count: null,
  })),
}));

describe("App", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the service control header", async () => {
    render(<App />);
    expect(
      screen.getByRole("heading", { name: "Service Control Deck" })
    ).toBeInTheDocument();
    expect(await screen.findAllByText("Online")).toHaveLength(5);
  });

  it("shows offline state when a service check fails", async () => {
    vi.mocked(endpoints.pingPriceService).mockRejectedValueOnce(
      new Error("service unavailable")
    );

    render(<App />);

    expect(await screen.findByText("Price Service")).toBeInTheDocument();
    expect(await screen.findByText("Offline")).toBeInTheDocument();
    expect(await screen.findByText("/api/prices/v1")).toBeInTheDocument();
  });
});

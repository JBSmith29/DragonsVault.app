import { render, screen } from "@testing-library/react";
import { vi } from "vitest";

import App from "../App";

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
  it("renders the service control header", () => {
    render(<App />);
    expect(
      screen.getByRole("heading", { name: "Service Control Deck" })
    ).toBeInTheDocument();
  });
});

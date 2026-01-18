import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
} from "react";
import {
  pingCardData,
  pingFolderService,
  pingPriceService,
  pingUserManager,
  pingWebApi,
  fetchCardDataSyncStatus,
  fetchMaintenanceStatus,
  type CardDataSyncStatus,
  type MaintenanceStatus,
} from "./api/endpoints";

type ServiceStatus = "checking" | "ok" | "error";

type ServiceHealth = {
  status: ServiceStatus;
  latencyMs?: number | null;
};

type SyncSource =
  | { kind: "maintenance"; key: keyof MaintenanceStatus; label: string }
  | { kind: "card-data"; label: string };

type ServiceDefinition = {
  key: string;
  name: string;
  summary: string;
  apiPrefix: string;
  schema: string;
  ping: () => Promise<{ status: string; service: string }>;
  syncSources?: SyncSource[];
  endpoint?: string;
};

const serviceDefinitions: ServiceDefinition[] = [
  {
    key: "web-api",
    name: "Web API",
    summary: "Flask monolith, admin tools, and legacy routes.",
    apiPrefix: "/api",
    schema: "public",
    ping: pingWebApi,
    endpoint: "/api/healthz",
    syncSources: [
      { kind: "maintenance", key: "scryfall", label: "Scryfall cache" },
      { kind: "maintenance", key: "spellbook", label: "Spellbook combos" },
      { kind: "maintenance", key: "fts", label: "FTS index" },
      { kind: "maintenance", key: "edhrec", label: "EDHREC cache" },
    ],
  },
  {
    key: "user-manager",
    name: "User Manager",
    summary: "Auth, tokens, and audit trails.",
    apiPrefix: "/api/user",
    schema: "user_manager",
    ping: pingUserManager,
  },
  {
    key: "card-data",
    name: "Card Data",
    summary: "Oracle-level Scryfall sync and annotations.",
    apiPrefix: "/api/cards",
    schema: "card_data",
    ping: pingCardData,
    syncSources: [{ kind: "card-data", label: "Oracle sync" }],
  },
  {
    key: "folder-service",
    name: "Folder Service",
    summary: "Decks and collections (currently routed to the monolith).",
    apiPrefix: "/api/folders",
    schema: "folder_service",
    ping: pingFolderService,
  },
  {
    key: "price-service",
    name: "Price Service",
    summary: "MTGJSON price normalization and caching.",
    apiPrefix: "/api/prices",
    schema: "price_service",
    ping: pingPriceService,
  },
];

const buildInitialHealth = () =>
  Object.fromEntries(
    serviceDefinitions.map((service) => [
      service.key,
      { status: "checking", latencyMs: null },
    ])
  ) as Record<string, ServiceHealth>;

const formatLatency = (value?: number | null) =>
  value === null || value === undefined ? "—" : `${Math.round(value)} ms`;

const formatSyncTime = (value?: string | null) => {
  if (!value) return "—";
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) {
    return value;
  }
  return dt.toLocaleString();
};

const formatSyncStatus = (value?: string | null) => {
  if (!value) return "—";
  const normalized = value.toLowerCase();
  if (normalized === "file") return "Cached";
  if (normalized === "disabled") return "Disabled";
  if (normalized === "ok") return "OK";
  if (normalized === "not_modified") return "Unchanged";
  if (normalized === "skipped") return "Skipped";
  if (normalized === "failed") return "Failed";
  if (normalized === "warning") return "Warning";
  return value;
};

export default function App() {
  const [services, setServices] = useState<Record<string, ServiceHealth>>(
    buildInitialHealth()
  );
  const [maintenance, setMaintenance] = useState<MaintenanceStatus | null>(null);
  const [cardDataSync, setCardDataSync] = useState<CardDataSyncStatus | null>(null);
  const [lastChecked, setLastChecked] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const serviceList = useMemo(() => serviceDefinitions, []);

  const refreshStatuses = useCallback(async () => {
    setBusy(true);
    setServices(buildInitialHealth());
    const maintenancePromise = fetchMaintenanceStatus();
    const cardDataPromise = fetchCardDataSyncStatus();

    const results = await Promise.allSettled(
      serviceList.map(async (service) => {
        const start = performance.now();
        try {
          const payload = await service.ping();
          const latencyMs = performance.now() - start;
          return {
            key: service.key,
            status: payload.status === "ok" ? "ok" : "error",
            latencyMs,
          };
        } catch (error) {
          const latencyMs = performance.now() - start;
          return { key: service.key, status: "error", latencyMs };
        }
      })
    );

    const next: Record<string, ServiceHealth> = {};
    results.forEach((result, index) => {
      const key = serviceList[index].key;
      if (result.status === "fulfilled") {
        next[key] = {
          status: result.value.status,
          latencyMs: result.value.latencyMs,
        };
      } else {
        next[key] = { status: "error", latencyMs: null };
      }
    });

    setServices(next);

    const [maintenanceResult, cardDataResult] = await Promise.allSettled([
      maintenancePromise,
      cardDataPromise,
    ]);
    setMaintenance(
      maintenanceResult.status === "fulfilled" ? maintenanceResult.value : null
    );
    setCardDataSync(
      cardDataResult.status === "fulfilled" ? cardDataResult.value : null
    );
    setLastChecked(new Date().toISOString());
    setBusy(false);
  }, [serviceList]);

  useEffect(() => {
    refreshStatuses();
  }, [refreshStatuses]);

  const syncRowsFor = useCallback(
    (service: ServiceDefinition) => {
      const sources = service.syncSources ?? [];
      if (!sources.length) return [];
      return sources.map((source) => {
        if (source.kind === "maintenance") {
          const entry = maintenance?.[source.key];
          const fallback =
            entry?.status && entry.status !== "unknown"
              ? formatSyncStatus(entry.status)
              : "—";
          return {
            label: source.label,
            value: entry?.last_sync
              ? formatSyncTime(entry.last_sync)
              : fallback,
          };
        }
        const stamp = cardDataSync?.processed_at || cardDataSync?.updated_at;
        const fallback = cardDataSync?.status
          ? formatSyncStatus(cardDataSync.status)
          : "—";
        return {
          label: source.label,
          value: stamp ? formatSyncTime(stamp) : fallback,
        };
      });
    },
    [maintenance, cardDataSync]
  );

  const lastCheckedLabel = lastChecked
    ? formatSyncTime(lastChecked)
    : "Never";

  return (
    <div className="page">
      <header className="hero">
        <div className="eyebrow">DragonsVault</div>
        <h1>Service Control Deck</h1>
        <p>
          Real-time status checks for every service behind the API gateway,
          including latency and the most recent data syncs.
        </p>
        <div className="hero-status">Last checked: {lastCheckedLabel}</div>
        <div className="actions">
          <button className="primary" onClick={refreshStatuses} disabled={busy}>
            {busy ? "Checking services..." : "Refresh status"}
          </button>
          <a className="ghost" href="/api/ops/maintenance">
            Open maintenance snapshot
          </a>
        </div>
      </header>

      <section className="services">
        {serviceList.map((service, index) => {
          const status = services[service.key]?.status ?? "checking";
          const latency = services[service.key]?.latencyMs ?? null;
          const syncRows = syncRowsFor(service);
          const endpoint = service.endpoint ?? `${service.apiPrefix}/v1`;
          return (
            <article
              key={service.key}
              className={`service-card status-${status}`}
              style={{ "--delay": `${index * 0.08}s` } as CSSProperties}
            >
              <div className="card-top">
                <h2>{service.name}</h2>
                <span className={`status-pill status-${status}`}>
                  {status === "ok"
                    ? "Online"
                    : status === "error"
                      ? "Offline"
                      : "Checking"}
                </span>
              </div>
              <p>{service.summary}</p>
              <div className="card-meta">
                <div className="meta-row">
                  <span className="meta-label">Latency</span>
                  <span className="meta-value">{formatLatency(latency)}</span>
                </div>
                {syncRows.map((row) => (
                  <div className="meta-row" key={`${service.key}-${row.label}`}>
                    <span className="meta-label">{row.label}</span>
                    <span className="meta-value">{row.value}</span>
                  </div>
                ))}
              </div>
              <div className="card-footer">
                <span className="endpoint">{endpoint}</span>
                <span className="schema">schema: {service.schema}</span>
              </div>
            </article>
          );
        })}
      </section>

      <footer className="footer">
        <span>Compose-only deployment, stateless services, shared Postgres.</span>
      </footer>
    </div>
  );
}

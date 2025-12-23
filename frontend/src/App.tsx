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
  pingUserManager,
} from "./api/endpoints";

type ServiceStatus = "checking" | "ok" | "error";

type ServiceDefinition = {
  key: string;
  name: string;
  summary: string;
  apiPrefix: string;
  schema: string;
  ping: () => Promise<{ status: string; service: string }>;
};

const serviceDefinitions: ServiceDefinition[] = [
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
    summary: "Scryfall cache, prints, and wishlist data.",
    apiPrefix: "/api/cards",
    schema: "card_data",
    ping: pingCardData,
  },
  {
    key: "folder-service",
    name: "Folder Service",
    summary: "Decks, collections, and imports.",
    apiPrefix: "/api/folders",
    schema: "folder_service",
    ping: pingFolderService,
  },
];

const buildInitialStatus = () =>
  Object.fromEntries(
    serviceDefinitions.map((service) => [service.key, "checking"])
  ) as Record<string, ServiceStatus>;

export default function App() {
  const [statuses, setStatuses] = useState<Record<string, ServiceStatus>>(
    buildInitialStatus()
  );
  const [busy, setBusy] = useState(false);
  const serviceList = useMemo(() => serviceDefinitions, []);

  const refreshStatuses = useCallback(async () => {
    setBusy(true);
    setStatuses(buildInitialStatus());

    const results = await Promise.allSettled(
      serviceList.map(async (service) => {
        const payload = await service.ping();
        return { key: service.key, status: payload.status };
      })
    );

    const next: Record<string, ServiceStatus> = {};
    results.forEach((result, index) => {
      const key = serviceList[index].key;
      if (result.status === "fulfilled" && result.value.status === "ok") {
        next[key] = "ok";
      } else {
        next[key] = "error";
      }
    });

    setStatuses(next);
    setBusy(false);
  }, [serviceList]);

  useEffect(() => {
    refreshStatuses();
  }, [refreshStatuses]);

  return (
    <div className="page">
      <header className="hero">
        <div className="eyebrow">DragonsVault</div>
        <h1>Service Control Deck</h1>
        <p>
          The new SPA is wired to call dedicated microservices. Each service
          publishes a simple ping endpoint so we can validate routing while we
          migrate real features.
        </p>
        <div className="actions">
          <button className="primary" onClick={refreshStatuses} disabled={busy}>
            {busy ? "Checking services..." : "Refresh status"}
          </button>
          <a className="ghost" href="/api/user/v1/ping">
            Open API ping
          </a>
        </div>
      </header>

      <section className="services">
        {serviceList.map((service, index) => {
          const status = statuses[service.key] ?? "checking";
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
              <div className="card-footer">
                <span className="endpoint">{service.apiPrefix}/v1</span>
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

import { apiGet } from "./client";

export type ServicePing = {
  status: string;
  service: string;
};

export type SyncStatus = {
  last_sync?: string | null;
  status?: string | null;
  source?: string | null;
};

export type MaintenanceStatus = {
  scryfall?: SyncStatus;
  rulings?: SyncStatus;
  spellbook?: SyncStatus;
  fts?: SyncStatus;
  edhrec?: SyncStatus;
};

export type CardDataSyncStatus = {
  status?: string;
  updated_at?: string | null;
  processed_at?: string | null;
  record_count?: number | null;
};

export const pingUserManager = () => apiGet<ServicePing>("/user/v1/ping");
export const pingCardData = () => apiGet<ServicePing>("/cards/v1/ping");
export const pingFolderService = () => apiGet<ServicePing>("/folders/v1/ping");
export const pingPriceService = () => apiGet<ServicePing>("/prices/v1/ping");
export const pingWebApi = () => apiGet<ServicePing>("/healthz");

export const fetchMaintenanceStatus = () => apiGet<MaintenanceStatus>("/ops/maintenance");
export const fetchCardDataSyncStatus = () =>
  apiGet<CardDataSyncStatus>("/cards/v1/scryfall/status");

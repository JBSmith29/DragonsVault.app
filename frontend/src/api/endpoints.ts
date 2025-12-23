import { apiGet } from "./client";

export type ServicePing = {
  status: string;
  service: string;
};

export const pingUserManager = () => apiGet<ServicePing>("/user/v1/ping");
export const pingCardData = () => apiGet<ServicePing>("/cards/v1/ping");
export const pingFolderService = () => apiGet<ServicePing>("/folders/v1/ping");

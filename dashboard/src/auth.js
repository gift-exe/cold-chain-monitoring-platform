// Sign-in through Keycloak (authorisation code flow with PKCE). The access token is short-lived and
// refreshed before each request; the API checks it on every call.
import Keycloak from "keycloak-js";

export const keycloak = new Keycloak({
  url: import.meta.env.VITE_KEYCLOAK_URL || "http://localhost:8080",
  realm: "coldchain",
  clientId: "coldchain-dashboard",
});

export function login() {
  return keycloak.init({ onLoad: "login-required", pkceMethod: "S256", checkLoginIframe: false });
}

export async function token() {
  await keycloak.updateToken(30);
  return keycloak.token;
}

export function logout() {
  keycloak.logout({ redirectUri: window.location.origin });
}

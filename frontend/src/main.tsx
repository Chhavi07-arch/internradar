import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";

function SessionGate() {
  const [state, setState] = useState<"checking" | "local" | "signed-in" | "signed-out" | "error">("checking");
  useEffect(() => {
    fetch("/api/session").then(async (response) => {
      if (response.status === 401) setState("signed-out");
      else if (response.ok) setState((await response.json()).local ? "local" : "signed-in");
      else setState("error");
    }).catch(() => setState("error"));
  }, []);
  if (state === "local") return <App />;
  if (state === "signed-in") return <App onSignOut={async () => {
    const response = await fetch("/auth/logout", { method: "POST" });
    if (!response.ok) throw new Error("Sign-out failed");
    setState("signed-out");
  }} />;
  return <main className="mx-auto max-w-md space-y-4 px-6 py-16">
    <h1 className="text-xl font-semibold">InternRadar</h1>
    {state === "signed-out" ? <a className="underline" href="/auth/login">Sign in with Google</a>
      : <p>{state === "error" ? "Could not check your session. Reload to retry." : "Checking your session…"}</p>}
  </main>;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <SessionGate />
  </StrictMode>,
);

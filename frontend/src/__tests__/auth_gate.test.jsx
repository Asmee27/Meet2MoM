// @vitest-environment jsdom
// Phase 7 UI tests: the sign-in screen appears only when the API says 401.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import AuthGate, { useAuth } from "../components/AuthGate";
import * as api from "../services/api";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function WhoAmI() {
  const { user } = useAuth();
  return <p>app content {user ? `for ${user.display_name}` : "(open mode)"}</p>;
}

const unauthorized = () => Object.assign(new Error("Sign in to continue."), { status: 401 });

describe("AuthGate", () => {
  it("shows the app directly when sign-in is not required", async () => {
    vi.spyOn(api, "getMe").mockResolvedValue({ auth_required: false, user: null });
    render(<AuthGate><WhoAmI /></AuthGate>);
    expect(await screen.findByText("app content (open mode)")).toBeTruthy();
  });

  it("asks a signed-out user to sign in and never renders the app first", async () => {
    vi.spyOn(api, "getMe").mockRejectedValue(unauthorized());
    const login = vi.spyOn(api, "login").mockResolvedValue({
      user: { user_id: "u_1", email: "a@example.test", display_name: "Alice" },
    });
    render(<AuthGate><WhoAmI /></AuthGate>);

    expect(await screen.findByRole("button", { name: "Sign in" })).toBeTruthy();
    expect(screen.queryByText(/app content/)).toBeNull();

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "a@example.test" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "correct horse 1" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("app content for Alice")).toBeTruthy();
    expect(login).toHaveBeenCalledWith("a@example.test", "correct horse 1");
  });

  it("shows the server's error for a wrong password", async () => {
    vi.spyOn(api, "getMe").mockRejectedValue(unauthorized());
    vi.spyOn(api, "login").mockRejectedValue(Object.assign(new Error("Email or password is incorrect."), { status: 401 }));
    render(<AuthGate><WhoAmI /></AuthGate>);
    fireEvent.change(await screen.findByLabelText("Email"), { target: { value: "a@example.test" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect((await screen.findByRole("alert")).textContent).toContain("incorrect");
    expect(screen.queryByText(/app content/)).toBeNull();
  });

  it("registers a new account with a name", async () => {
    vi.spyOn(api, "getMe").mockRejectedValue(unauthorized());
    const register = vi.spyOn(api, "register").mockResolvedValue({
      user: { user_id: "u_2", email: "b@example.test", display_name: "Bob" },
    });
    render(<AuthGate><WhoAmI /></AuthGate>);
    fireEvent.click(await screen.findByText("New here? Create an account"));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Bob" } });
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "b@example.test" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "long-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByText("app content for Bob")).toBeTruthy();
    expect(register).toHaveBeenCalledWith("b@example.test", "long-password", "Bob");
  });
});

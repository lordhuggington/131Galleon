import { useActionState, useEffect, useState } from "react";
import { api } from "../../api/client";
import type { MeResponse, OkResponse } from "../../api/types";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { Field } from "../../components/Field";
import { SunIcon } from "../../components/illustrations/SunIcon";
import { SunsetBand } from "../../components/illustrations/SunsetBand";
import { WaveDivider } from "../../components/illustrations/WaveDivider";
import { prettyPhone } from "../../lib/phone";
import { useApp } from "../../state/AppState";

const RESEND_SECONDS = 30;

/** `smsEnabled` is null until GET /api/login/options answers: show neither path yet. */
export function LoginScreen({ smsEnabled }: { smsEnabled: boolean | null }) {
  const { signIn } = useApp();
  const [step, setStep] = useState<"phone" | "code">("phone");
  const [phone, setPhone] = useState("");
  const [sentTo, setSentTo] = useState("");
  const [code, setCode] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [cooldown, setCooldown] = useState(0);

  useEffect(() => {
    if (cooldown <= 0) return;
    const id = window.setTimeout(() => setCooldown((s) => s - 1), 1000);
    return () => window.clearTimeout(id);
  }, [cooldown]);

  const [startError, startAction, starting] = useActionState<string | null, FormData>(async () => {
    try {
      await api<OkResponse>("POST", "/api/login/sms/start", { phone });
      setSentTo(phone);
      setStep("code");
      setCooldown(RESEND_SECONDS);
      return null;
    } catch (e) {
      return e instanceof Error ? e.message : "Couldn't send the text message. Try again in a minute.";
    }
  }, null);

  const [checkError, checkAction, checking] = useActionState<string | null, FormData>(async () => {
    try {
      const { me } = await api<MeResponse>("POST", "/api/login/sms/check", { phone: sentTo, code });
      await signIn(me);
      return null;
    } catch (e) {
      return e instanceof Error ? e.message : "That code isn't right or has expired.";
    }
  }, null);

  const [passwordError, passwordAction, signingIn] = useActionState<string | null, FormData>(async () => {
    try {
      const { me } = await api<MeResponse>("POST", "/api/login", {
        username: username.trim(),
        password,
      });
      setPassword("");
      await signIn(me);
      return null;
    } catch (e) {
      return e instanceof Error ? e.message : "Wrong username or password.";
    }
  }, null);

  return (
    <div className="login-wrap">
      <SunsetBand>
        <h1>House Run Sheet</h1>
      </SunsetBand>
      <WaveDivider />

      {smsEnabled === null ? null : smsEnabled === false ? (
        <Card>
          <p className="small muted" style={{ margin: 0 }}>
            Text-message sign-in isn't set up on this server yet.
          </p>
        </Card>
      ) : step === "phone" ? (
        <Card className="login">
          <form className="login" action={startAction}>
            <Field label="Your phone number" htmlFor="loginPhone" hint="US mobile, e.g. (310) 555-1234">
              <input
                id="loginPhone"
                type="tel"
                autoComplete="tel"
                inputMode="tel"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                required
              />
            </Field>
            {startError ? (
              <div className="err" role="alert">
                {startError}
              </div>
            ) : null}
            <Button type="submit" variant="primary" disabled={starting}>
              Text me a code
            </Button>
          </form>
        </Card>
      ) : (
        <Card className="login">
          <form className="login" action={checkAction}>
            <Field label={`Enter the 6-digit code we texted to ${prettyPhone(sentTo) || sentTo}`} htmlFor="loginCode">
              <input
                id="loginCode"
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={6}
                value={code}
                onChange={(e) => setCode(e.target.value)}
                required
              />
            </Field>
            {checkError ? (
              <div className="err" role="alert">
                {checkError}
              </div>
            ) : null}
            <Button type="submit" variant="primary" disabled={checking}>
              Sign in
            </Button>
          </form>
          <div className="row">
            <Button
              variant="ghost"
              disabled={cooldown > 0 || starting}
              onClick={() => startAction(new FormData())}
            >
              {cooldown > 0 ? `Send it again (${cooldown}s)` : "Send it again"}
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setStep("phone");
                setCode("");
              }}
            >
              Use a different number
            </Button>
          </div>
          {startError ? (
            <div className="err" role="alert">
              {startError}
            </div>
          ) : null}
        </Card>
      )}

      {smsEnabled === true ? (
        <Button variant="ghost" aria-expanded={showPassword} onClick={() => setShowPassword((v) => !v)}>
          Owner? Sign in with password
        </Button>
      ) : null}

      {showPassword || smsEnabled === false ? (
        <Card className="login">
          <form className="login" action={passwordAction}>
            <Field label="Username" htmlFor="loginUser">
              <input
                id="loginUser"
                type="text"
                autoComplete="username"
                autoCapitalize="none"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
              />
            </Field>
            <Field label="Password" htmlFor="loginPass">
              <input
                id="loginPass"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </Field>
            {passwordError ? (
              <div className="err" role="alert">
                {passwordError}
              </div>
            ) : null}
            <Button type="submit" variant="primary" disabled={signingIn}>
              Sign in
            </Button>
          </form>
        </Card>
      ) : null}

      <div className="row" style={{ justifyContent: "center" }}>
        <SunIcon size={32} />
      </div>
    </div>
  );
}

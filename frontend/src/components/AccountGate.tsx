import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import {
  ACCOUNT_EXPIRED_EVENT,
  ApiError,
  canCreateAdmin,
  createFirstAdmin,
  fetchCurrentAccount,
  loginAccount,
  logoutAccount,
  setRequestAccount,
} from "../api";
import type { WebAccount } from "../types";
import { ThemeToggleButton } from "./ThemeToggleButton";

const ACCOUNT_CHANGE_KEY = "agent-workbench-account-changed";
const INPUT_STYLE = "w-full rounded-xl border border-neutral-200 bg-white px-3 py-2.5 font-normal text-neutral-900 placeholder:text-neutral-400 focus:border-violet-500 focus:outline-none dark:border-white/[0.12] dark:bg-[#090A12] dark:text-[#F3F4FA] dark:placeholder:text-[#6E7490] dark:focus:border-violet-400";

function announceAccountChange() {
  try {
    localStorage.setItem(ACCOUNT_CHANGE_KEY, `${Date.now()}-${Math.random()}`);
  } catch {
    // 请求也会校验页面当前显示的账号。
  }
}

export function AccountGate({ children }: {
  children: (account: WebAccount, onLogout: () => Promise<void>) => ReactNode;
}) {
  const [account, setAccount] = useState<WebAccount | null>();
  const [setup, setSetup] = useState(false);
  const [connectionError, setConnectionError] = useState("");
  const requestVersion = useRef(0);

  const showAccount = useCallback((next?: WebAccount | null) => {
    setRequestAccount(next?.mode === "shared" ? next.id : null);
    setAccount(next);
  }, []);

  const refresh = useCallback(async () => {
    const version = ++requestVersion.current;
    setConnectionError("");
    try {
      const current = await fetchCurrentAccount().catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 401) return null;
        throw err;
      });
      const needsSetup = current === null && await canCreateAdmin();
      if (version !== requestVersion.current) return;
      setSetup(needsSetup);
      showAccount(current);
    } catch (err) {
      if (version !== requestVersion.current) return;
      showAccount();
      setConnectionError(err instanceof Error ? err.message : "无法连接工作台。");
    }
  }, [showAccount]);

  useEffect(() => {
    const verify = () => void refresh();
    const accountChanged = () => {
      showAccount();
      verify();
    };
    const onStorage = (event: StorageEvent) => {
      if (event.key === ACCOUNT_CHANGE_KEY) accountChanged();
    };
    const onPageShow = (event: PageTransitionEvent) => {
      if (event.persisted) accountChanged();
    };
    verify();
    window.addEventListener("focus", verify);
    window.addEventListener("storage", onStorage);
    window.addEventListener("pageshow", onPageShow);
    window.addEventListener(ACCOUNT_EXPIRED_EVENT, accountChanged);
    return () => {
      requestVersion.current += 1;
      window.removeEventListener("focus", verify);
      window.removeEventListener("storage", onStorage);
      window.removeEventListener("pageshow", onPageShow);
      window.removeEventListener(ACCOUNT_EXPIRED_EVENT, accountChanged);
    };
  }, [refresh, showAccount]);

  if (account) {
    return children(account, async () => {
      await logoutAccount();
      requestVersion.current += 1;
      setSetup(false);
      showAccount(null);
      announceAccountChange();
    });
  }

  return (
    <main className="account-astra-backdrop relative grid min-h-dvh place-items-center bg-[#faf9fc] px-5 py-10 text-neutral-900 dark:text-[#F3F4FA]">
      <ThemeToggleButton
        className="fixed right-4 top-4 h-9 w-9 bg-white/90 backdrop-blur-md dark:bg-white/[0.06]"
        iconClassName="h-4 w-4"
      />
      <section className="w-full max-w-sm rounded-[28px] border border-violet-100 bg-white p-7 shadow-[0_20px_70px_rgba(67,34,109,0.09)] dark:border-white/[0.11] dark:bg-[#11131F]/88 dark:backdrop-blur-xl dark:shadow-[0_28px_84px_rgba(0,0,0,0.72),inset_0_1px_0_rgba(255,255,255,0.08)] sm:p-9">
        <div className="mb-5 grid h-10 w-10 place-items-center rounded-xl bg-neutral-950 text-sm font-semibold text-white dark:bg-[#F4F5FA] dark:text-[#090A10]" aria-hidden="true">智</div>
        <h1 className="text-2xl font-semibold tracking-tight">
          {setup && account === null ? "创建管理员账号" : "登录智启 Agent"}
        </h1>
        {account === null ? (
          <AccountForm
            key={setup ? "setup" : "login"}
            setup={setup}
            onSubmit={async (username, password) => {
              try {
                const authenticate = setup ? createFirstAdmin : loginAccount;
                const current = await authenticate(username, password);
                requestVersion.current += 1;
                setSetup(false);
                showAccount(current);
                announceAccountChange();
              } catch (err) {
                if (!setup || !(err instanceof ApiError) || err.status !== 409) throw err;
                await refresh();
              }
            }}
          />
        ) : connectionError ? (
          <div className="mt-5 space-y-4">
            <p role="alert" className="text-sm text-rose-700 dark:text-rose-300">{connectionError}</p>
            <button type="button" onClick={() => void refresh()} className="rounded-xl bg-neutral-900 px-4 py-2 text-sm font-medium text-white dark:bg-[#F4F5FA] dark:text-[#090A10] dark:hover:bg-white">
              重新连接
            </button>
          </div>
        ) : (
          <p className="mt-3 text-sm text-neutral-500 dark:text-[#8E94B0]" role="status">正在检查登录状态…</p>
        )}
      </section>
    </main>
  );
}

function AccountForm({ setup, onSubmit }: {
  setup: boolean;
  onSubmit: (username: string, password: string) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) return;
    const data = new FormData(event.currentTarget);
    const username = String(data.get("username") ?? "").trim();
    const password = String(data.get("password") ?? "");
    if (setup && password !== data.get("confirmPassword")) {
      setError("两次输入的密码不一致。");
      return;
    }
    setError("");
    setBusy(true);
    try {
      await onSubmit(username, password);
    } catch (err) {
      setError(err instanceof Error ? err.message : "提交失败，请重试。");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={(event) => void submit(event)} className="mt-5 space-y-4">
      <label className="block space-y-1.5 text-sm font-medium">
        用户名
        <input
          name="username"
          autoComplete="username"
          required
          maxLength={40}
          pattern={setup ? "[A-Za-z0-9][A-Za-z0-9_.\\-]{2,39}" : undefined}
          title={setup ? "使用 3–40 个字母、数字、点、下划线或连字符，以字母或数字开头" : undefined}
          className={INPUT_STYLE}
        />
      </label>
      <label className="block space-y-1.5 text-sm font-medium">
        {setup ? "密码（至少 15 个字符）" : "密码"}
        <input name="password" type="password" autoComplete={setup ? "new-password" : "current-password"} required minLength={setup ? 15 : undefined} maxLength={256} className={INPUT_STYLE} />
      </label>
      {setup && (
        <label className="block space-y-1.5 text-sm font-medium">
          确认密码
          <input name="confirmPassword" type="password" autoComplete="new-password" required maxLength={256} className={INPUT_STYLE} />
        </label>
      )}
      {error && <p role="alert" className="text-sm text-rose-700 dark:text-rose-300">{error}</p>}
      <button type="submit" disabled={busy} className="w-full rounded-xl bg-neutral-950 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-neutral-800 disabled:opacity-50 dark:bg-[#F4F5FA] dark:text-[#090A10] dark:hover:bg-white">
        {setup ? (busy ? "正在创建…" : "创建并登录") : (busy ? "正在登录…" : "登录")}
      </button>
      <p className="text-center text-xs text-neutral-500 dark:text-[#8E94B0]">
        {setup ? "创建后可在右上角账号菜单为成员开通账号。" : "请向工作台管理员获取账号。"}
      </p>
    </form>
  );
}

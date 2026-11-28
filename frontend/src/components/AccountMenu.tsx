import { LogOut, UserRound } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { createWorkbenchUser } from "../api";
import type { WebAccount } from "../types";

export function AccountMenu({ account, onLogout }: {
  account: WebAccount;
  onLogout: () => Promise<void>;
}) {
  const root = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [createdAccount, setCreatedAccount] = useState("");

  useEffect(() => {
    if (!open) return;
    const close = () => {
      setOpen(false);
      setCreating(false);
      setError("");
      setCreatedAccount("");
    };
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) close();
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    setBusy(true);
    setError("");
    setCreatedAccount("");
    try {
      const member = await createWorkbenchUser(String(data.get("username") ?? "").trim(), String(data.get("password") ?? ""));
      form.reset();
      setCreatedAccount(member.username);
    } catch (err) {
      setError(err instanceof Error ? err.message : "开通账号失败。");
    } finally {
      setBusy(false);
    }
  };

  const logout = async () => {
    setBusy(true);
    setError("");
    try {
      await onLogout();
    } catch (err) {
      setError(err instanceof Error ? err.message : "退出登录失败。");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div ref={root} className="relative">
      <button
        type="button"
        title={`工作台账号 ${account.username}`}
        aria-label={`工作台账号 ${account.username}`}
        aria-expanded={open}
        onClick={() => {
          setOpen(!open);
          setCreating(false);
          setError("");
          setCreatedAccount("");
        }}
        className="inline-flex h-8 items-center gap-1.5 rounded-full border border-neutral-200 px-2 text-xs text-neutral-600 transition hover:bg-neutral-100 dark:border-white/[0.11] dark:bg-white/[0.05] dark:text-[#C8CCE0] dark:hover:bg-white/[0.1] dark:hover:text-[#F3F4FA]"
      >
        <UserRound className="h-4 w-4 shrink-0" />
        <span className="hidden max-w-24 truncate sm:inline">{account.username}</span>
      </button>
      {open && (
        <div className="absolute right-0 top-full z-[70] mt-2 w-64 max-w-[calc(100vw-1.5rem)] rounded-2xl border border-neutral-200 bg-white p-3 shadow-xl dark:border-white/[0.11] dark:bg-[#171A29] dark:text-[#E5E8F5] dark:shadow-[0_18px_48px_rgba(0,0,0,0.68)]">
          <p className="break-all px-1 text-sm font-medium text-neutral-900 dark:text-[#F3F4FA]">{account.username}</p>
          {account.role === "admin" && (
            <div className="mt-2 border-t border-neutral-100 pt-2 dark:border-white/[0.08]">
              <button type="button" aria-expanded={creating} disabled={busy} onClick={() => setCreating(!creating)} className="w-full rounded-lg px-2 py-1.5 text-left text-sm hover:bg-neutral-100 dark:hover:bg-white/[0.07]">
                开通成员账号
              </button>
              {creating && (
                <form onSubmit={(event) => void submit(event)} className="mt-2 space-y-2 px-1">
                  <label className="block space-y-1 text-xs">
                    新成员用户名
                    <input name="username" autoComplete="off" required minLength={3} maxLength={40} pattern="[A-Za-z0-9][A-Za-z0-9_.\-]{2,39}" className="w-full rounded-lg border border-neutral-200 bg-white px-2 py-1.5 dark:border-white/[0.12] dark:bg-[#090A12] dark:text-[#F3F4FA]" />
                  </label>
                  <label className="block space-y-1 text-xs">
                    初始密码（至少 15 个字符）
                    <input name="password" type="password" autoComplete="new-password" required minLength={15} maxLength={256} className="w-full rounded-lg border border-neutral-200 bg-white px-2 py-1.5 dark:border-white/[0.12] dark:bg-[#090A12] dark:text-[#F3F4FA]" />
                  </label>
                  <button type="submit" disabled={busy} className="rounded-lg bg-neutral-900 px-3 py-1.5 text-xs text-white disabled:opacity-50 dark:bg-[#F4F5FA] dark:text-[#090A10] dark:hover:bg-white">创建账号</button>
                </form>
              )}
              {createdAccount && <p role="status" className="mt-2 px-1 text-xs text-emerald-700 dark:text-emerald-300">已开通 {createdAccount}。</p>}
            </div>
          )}
          {error && <p role="alert" className="mt-2 px-1 text-xs text-rose-700 dark:text-rose-300">{error}</p>}
          <button type="button" disabled={busy} onClick={() => void logout()} className="mt-2 flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm text-neutral-700 transition hover:bg-neutral-100 disabled:opacity-50 dark:text-[#C8CCE0] dark:hover:bg-white/[0.07] dark:hover:text-[#F3F4FA]">
            <LogOut className="h-4 w-4" />
            退出登录
          </button>
        </div>
      )}
    </div>
  );
}

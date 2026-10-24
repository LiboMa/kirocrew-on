import { useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Pencil, Undo2, UserPlus } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { keys, useQuotas } from "@/api/hooks";
import type { Quotas } from "@/api/types";
import { FormDialog } from "@/components/FormDialog";
import { ListCard, RowActions } from "@/components/ListCard";
import { NativeSelect } from "@/components/NativeSelect";
import { Panel } from "@/components/Panel";
import { useRowMotion } from "@/components/rowMotion";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useConsole } from "@/features/console/context";

type Scope = "default" | "user" | "team";
/** A limit someone may change: its row (``quota``), whose it is, how it is sent and what it is now. */
interface Limit { quota: string; label: string; scope: Scope; id?: string; value: number | null }
type Open = { kind: "set" | "remove"; limit: Limit } | { kind: "person" } | null;

const shown = (limit: number | null | undefined) => (limit == null ? "不限" : String(limit));
const inRange = (text: string) => {
  const n = Number(text);
  return text.trim() !== "" && Number.isInteger(n) && n >= 1 && n <= 50;
};
const OUT_OF_RANGE = "上限须为 1–50 的整数";

/** Sends one limit (``null`` removes it) and keeps what the server answers: every limit, read again. */
function useSave() {
  const client = useQueryClient();
  return async (scope: Scope, id: string | undefined, running: number | null) => {
    const answer = await api<Quotas>("/quotas", "POST", { scope, ...(id ? { id } : {}), running });
    client.setQueryData(keys.quotas, answer);
  };
}

/** 并发上限 (D61): how many tasks one runs at once, one's Teams too; administrators and Team owners set them. The page
 * shows the values; changing or removing one, and giving a person their own, happen in dialogs (PRD 2026-10-23,
 * 管理 / 我的设置 in dialogs). */
export function QuotasPanel() {
  const { users, teams: mine } = useConsole();
  const { data, error } = useQuotas();
  const save = useSave();
  const [dialog, setDialog] = useState<Open>(null);
  const motion = useRowMotion();
  const admin = data?.default !== undefined;
  const limited = new Set((data?.users ?? []).map((u) => u.id));
  const member = new Set(mine.map((t) => t.id));
  const own = (data?.teams ?? []).filter((t) => !admin || member.has(t.id));
  const others = admin ? (data?.teams ?? []).filter((t) => !member.has(t.id)) : [];

  /** The 操作 cell of a limit one may change: 「修改」 beside it, 「移除上限」 in 「⋯」 while there is one to remove. */
  const actions = (limit: Limit) => (
    <RowActions menu={"上限操作：" + limit.label}
      actions={[{ icon: Pencil, label: "修改", name: "修改上限：" + limit.label, onSelect: () => setDialog({ kind: "set", limit }) }]}
      more={limit.value == null ? [] : [{ icon: Undo2, label: "移除上限", name: "移除上限：" + limit.label,
        onSelect: () => setDialog({ kind: "remove", limit }) }]} />
  );
  const teamRow = (team: Quotas["teams"][number]) => {
    const limit: Limit = { quota: `team:${team.id}`, label: `Team「${team.name}」`, scope: "team", id: team.id, value: team.limit };
    return <Row key={team.id} motion={motion} quota={limit.quota} label={limit.label} values={[team.used, shown(team.limit)]}
      action={team.manage ? actions(limit) : <span className="text-xs text-muted-foreground">由 Team owner 设置</span>} />;
  };
  const running = ["运行中", "上限"];
  /** What removing a limit leaves, said before it is removed. */
  const removal = (limit: Limit) => limit.scope === "user" ? `移除后，${limit.label} 改用每人默认的上限（${shown(data?.default)}）。`
    : limit.scope === "default" ? "移除后，没有单独设置上限的人不再受限制。" : `移除后，${limit.label}不再限制同时运行的任务数。`;

  return (
    <Panel title="并发上限"
      description="同时运行的任务数上限。到了上限，会话的下一个任务按排队先后等待，有任务结束后自动开始，不必重新提交。没有设的范围不限制。每人默认与个人的上限由平台管理员设置，Team 的上限由 Team owner 或平台管理员设置。"
      actions={admin ? <Button type="button" onClick={() => setDialog({ kind: "person" })}><UserPlus />单独设置上限</Button> : undefined}>
      {error ? <p className="text-sm text-destructive">读取失败：{error.message}</p> : !data ? <p className="text-sm text-muted-foreground">读取中…</p> : (
        <div className="space-y-6">
          <Group title="我和我的 Team" columns={running}>
            <Row motion={motion} quota="mine" label="我" values={[data.mine.used, shown(data.mine.limit)]}
              action={<span className="text-xs text-muted-foreground">由平台管理员设置</span>} />
            {own.map(teamRow)}
          </Group>
          {others.length > 0 && (
            <Group title="其他 Team" heading="其他 Team（你不是成员，以管理员身份设置）" columns={running}>{others.map(teamRow)}</Group>
          )}
          {admin && (
            <Group title="每人的上限" columns={["上限"]}>
              <Row motion={motion} quota="default" label="每人默认" values={[shown(data.default)]}
                action={actions({ quota: "default", label: "每人默认", scope: "default", value: data.default ?? null })} />
              {(data.users ?? []).map((user) => (
                <Row key={user.id} motion={motion} quota={`user:${user.id}`} values={[String(user.limit)]}
                  label={<>{user.name}<small className="ml-2 text-xs text-muted-foreground">单独设置，优先于默认</small></>}
                  action={actions({ quota: `user:${user.id}`, label: user.name, scope: "user", id: user.id, value: user.limit })} />
              ))}
            </Group>
          )}
        </div>
      )}
      {dialog?.kind === "set" && (
        <LimitDialog limit={dialog.limit} onClose={() => setDialog(null)} onSaved={(n) => {
          setDialog(null);
          motion.flash(dialog.limit.quota);
          toast.success(`已保存上限：${dialog.limit.label} ${n}`);
        }} />
      )}
      {dialog?.kind === "remove" && (
        <FormDialog title="移除上限" submitLabel="移除上限" onClose={() => setDialog(null)} description={removal(dialog.limit)}
          onSubmit={async () => {
            const { limit } = dialog;
            if (limit.scope === "user") {   // the person's row goes: they are back on the default
              setDialog(null);
              await motion.leave(limit.quota, () => save(limit.scope, limit.id, null));
            } else {
              await save(limit.scope, limit.id, null);
              setDialog(null);
              motion.flash(limit.quota);
            }
            toast.success("已移除上限：" + limit.label);
          }} />
      )}
      {dialog?.kind === "person" && (
        <PersonDialog people={users.filter((u) => !limited.has(u.id))} onClose={() => setDialog(null)} onSaved={(id, name, n) => {
          setDialog(null);
          motion.flash(`user:${id}`);
          toast.success(`已保存上限：${name} ${n}`);
        }} />
      )}
    </Panel>
  );
}

/** One group of limits, a list under its heading; ``title`` names the list, ``heading`` (if longer) is what it shows. */
function Group({ title, heading, columns, children }: { title: string; heading?: string; columns: string[]; children: ReactNode }) {
  return (
    <div className="space-y-2">
      <h3 className="text-sm font-medium">{heading ?? title}</h3>
      <ListCard label={title}>
        <Table>
          <TableHeader>
            <TableRow className="bg-muted/40 hover:bg-muted/40">
              <TableHead className="pl-4">范围</TableHead>
              {columns.map((c) => <TableHead key={c}>{c}</TableHead>)}
              <TableHead className="pr-4 text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>{children}</TableBody>
        </Table>
      </ListCard>
    </div>
  );
}

function Row({ quota, label, values, action, motion }: {
  quota: string; label: ReactNode; values: (string | number)[]; action: ReactNode; motion: ReturnType<typeof useRowMotion>;
}) {
  const motionProps = motion.props(quota);
  return (
    <TableRow data-quota={quota} data-fresh={motionProps["data-fresh"]} className={motionProps.className}>
      <TableCell className="pl-4 font-medium">{label}</TableCell>
      {values.map((value, index) => <TableCell key={index} className="tabular-nums">{value}</TableCell>)}
      <TableCell className="pr-4 text-right">{action}</TableCell>
    </TableRow>
  );
}

/** The number field of both dialogs. Without min / max: the range is checked on submit, so that a wrong number is
 * explained in the dialog the way a refusal is, rather than by the browser's own bubble. */
function LimitField({ id, value, onChange, autoFocus }: { id: string; value: string; onChange: (value: string) => void; autoFocus?: boolean }) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>上限</Label>
      <Input id={id} type="number" inputMode="numeric" autoFocus={autoFocus} className="w-28" value={value} placeholder="1–50"
        onChange={(e) => onChange(e.target.value)} />
      <p className="text-xs text-muted-foreground">同时运行的任务数，1–50 的整数。</p>
    </div>
  );
}

/** Change one limit: a number 1–50, checked before it is sent; a refusal keeps the dialog open with it. */
function LimitDialog({ limit, onClose, onSaved }: { limit: Limit; onClose: () => void; onSaved: (n: number) => void }) {
  const save = useSave();
  const [text, setText] = useState(limit.value == null ? "" : String(limit.value));
  return (
    <FormDialog title="修改上限" submitLabel="保存上限" onClose={onClose}
      description={<>{limit.label}同时运行的任务数上限{limit.value == null ? "，现在不限" : `，现在是 ${limit.value}`}。</>}
      onSubmit={async () => {
        if (!inRange(text)) throw new Error(OUT_OF_RANGE);
        await save(limit.scope, limit.id, Number(text));
        onSaved(Number(text));
      }}>
      <LimitField id="quota-limit" autoFocus value={text} onChange={setText} />
    </FormDialog>
  );
}

/** Give one person their own limit, ahead of the default (administrators): the person, then the number. */
function PersonDialog({ people, onClose, onSaved }: {
  people: { id: string; username: string }[]; onClose: () => void; onSaved: (id: string, name: string, n: number) => void;
}) {
  const save = useSave();
  const [person, setPerson] = useState("");
  const [text, setText] = useState("2");
  return (
    <FormDialog title="单独设置上限" submitLabel="保存上限" onClose={onClose}
      description="为某人单独设一个上限，优先于每人默认。"
      onSubmit={async () => {
        if (!person) throw new Error("请选择用户");
        if (!inRange(text)) throw new Error(OUT_OF_RANGE);
        await save("user", person, Number(text));
        onSaved(person, people.find((u) => u.id === person)?.username ?? person, Number(text));
      }}>
      <div className="space-y-1.5">
        <Label htmlFor="quota-person">用户</Label>
        <NativeSelect id="quota-person" autoFocus value={person} onChange={(e) => setPerson(e.target.value)}>
          <option value="">选择用户…</option>
          {people.map((u) => <option key={u.id} value={u.id}>{u.username}</option>)}
        </NativeSelect>
      </div>
      <LimitField id="quota-person-limit" value={text} onChange={setText} />
    </FormDialog>
  );
}

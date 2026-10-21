import { useEffect, useState } from "react";
import { SlidersHorizontal, X } from "lucide-react";
import { NativeSelect } from "@/components/NativeSelect";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useConsole } from "@/features/console/context";
import { TOOLS } from "@/lib/labels";
import { CLIENT_LABELS } from "@/lib/policies";
import { FILTER_LABELS, STAGE_LABELS, activeFilters, type FilterKey, type RunListFilters } from "@/lib/runList";
import { cn } from "@/lib/utils";

const shown = (key: FilterKey, value: string) =>
  key === "stage" ? STAGE_LABELS[value] ?? value : key === "client" ? CLIENT_LABELS[value as keyof typeof CLIENT_LABELS] ?? value : value;

/** A text filter applied once typing pauses, so a query is not sent for every character. */
function TextFilter({ id, field, filters, setFilters, placeholder }: { id: string; field: "repo" | "branch";
  filters: RunListFilters; setFilters: (f: RunListFilters) => void; placeholder: string }) {
  const [text, setText] = useState(filters[field]);
  useEffect(() => setText(filters[field]), [filters, field]);
  useEffect(() => {
    if (text.trim() === filters[field]) return;
    const timer = setTimeout(() => setFilters({ ...filters, [field]: text.trim() }), 400);
    return () => clearTimeout(timer);
  }, [text, filters, field, setFilters]);
  return (
    <div className="space-y-1">
      <Label htmlFor={id} className="text-[11px]">{FILTER_LABELS[field]}</Label>
      <Input id={id} value={text} onChange={(e) => setText(e.target.value)} placeholder={placeholder} className="h-8 bg-card text-xs" />
    </div>
  );
}

/** D56: the session list's filters beyond the search and the state group — the panel and the labels of those chosen. */
export function RunFilters() {
  const { filters, setFilters, users } = useConsole();
  const [open, setOpen] = useState(false);
  const chosen = activeFilters(filters);
  const pick = (key: FilterKey) => (event: { target: { value: string } }) => setFilters({ ...filters, [key]: event.target.value });
  const select = (key: FilterKey, options: [string, string][]) => (
    <div className="space-y-1">
      <Label htmlFor={"filter-" + key} className="text-[11px]">{FILTER_LABELS[key]}</Label>
      <NativeSelect id={"filter-" + key} value={filters[key]} onChange={pick(key)} className="h-8 text-xs">
        <option value="">不限</option>
        {options.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </NativeSelect>
    </div>
  );

  return (
    <div className="space-y-2">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
        className={cn("flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs transition-colors",
          open || chosen.length ? "border-primary/40 text-primary" : "bg-card text-muted-foreground hover:bg-accent")}>
        <SlidersHorizontal className="size-3.5" aria-hidden />筛选{chosen.length ? <span aria-hidden>（{chosen.length}）</span> : null}
      </button>
      {open && (
        <div className="grid grid-cols-2 gap-2 rounded-lg border bg-card/60 p-2.5">
          <div className="col-span-2"><TextFilter id="filter-repo" field="repo" filters={filters} setFilters={setFilters}
            placeholder="github.com/o/r 或其中一段" /></div>
          <div className="col-span-2"><TextFilter id="filter-branch" field="branch" filters={filters} setFilters={setFilters}
            placeholder="源码 ref 或发布分支" /></div>
          {select("stage", Object.entries(STAGE_LABELS))}
          {select("tool", Object.keys(TOOLS).map((tool) => [tool, tool]))}
          {select("client", Object.entries(CLIENT_LABELS))}
          {select("submitter", users.map((user) => [user.username, user.username]))}
          <div className="space-y-1">
            <Label htmlFor="filter-since" className="text-[11px]">{FILTER_LABELS.since}</Label>
            <Input id="filter-since" type="date" value={filters.since} onChange={pick("since")} className="h-8 bg-card text-xs" />
          </div>
          <div className="space-y-1">
            <Label htmlFor="filter-until" className="text-[11px]">{FILTER_LABELS.until}</Label>
            <Input id="filter-until" type="date" value={filters.until} onChange={pick("until")} className="h-8 bg-card text-xs" />
          </div>
        </div>
      )}
      {chosen.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="已选筛选">
          {chosen.map((key) => {
            const text = `${FILTER_LABELS[key]}：${shown(key, filters[key])}`;
            return (
              <button key={key} type="button" aria-label={"移除 " + text} onClick={() => setFilters({ ...filters, [key]: "" })}
                className="flex items-center gap-1 rounded-full border bg-card px-2 py-0.5 text-[11px] text-muted-foreground hover:bg-accent">
                {text}<X className="size-3" aria-hidden />
              </button>
            );
          })}
          <button type="button" onClick={() => setFilters({ ...filters, ...Object.fromEntries(chosen.map((key) => [key, ""])) })}
            className="px-1 text-[11px] text-primary hover:underline">清除筛选</button>
        </div>
      )}
    </div>
  );
}

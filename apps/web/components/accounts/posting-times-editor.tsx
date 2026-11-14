"use client";

import type { PostingTime } from "@mera/sdk";
import { Icon } from "@/components/icons";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/field";
import { DAY_NAMES } from "@/lib/level-settings";

/*
 * Editable list of weekly posting slots ({day 0 = Monday, time "HH:MM"}).
 * Controlled: the parent owns the rows and decides when to save. An empty
 * list means "inherit" (the group's slots, then the workspace's).
 */
export function PostingTimesEditor({
  value,
  onChange,
  disabled,
  labelledBy,
}: {
  value: readonly PostingTime[];
  onChange: (next: PostingTime[]) => void;
  disabled?: boolean;
  labelledBy: string;
}) {
  const update = (i: number, patch: Partial<PostingTime>) =>
    onChange(value.map((row, j) => (j === i ? { ...row, ...patch } : row)));
  const remove = (i: number) => onChange(value.filter((_, j) => j !== i));
  const add = () => {
    const last = value[value.length - 1];
    onChange([...value, { day: last ? (last.day + 1) % 7 : 0, time: last?.time ?? "09:00" }]);
  };

  return (
    <div role="group" aria-labelledby={labelledBy} className="grid gap-2">
      {value.length === 0 ? (
        <p className="text-caption text-fg-subtle">No slots of its own: it uses its group&apos;s, then the workspace&apos;s.</p>
      ) : (
        <ul className="grid gap-1.5">
          {value.map((row, i) => (
            <li key={i} className="flex items-center gap-2">
              <Select
                aria-label={`Slot ${i + 1} day`}
                value={String(row.day)}
                disabled={disabled}
                onChange={(e) => update(i, { day: Number(e.target.value) })}
                className="min-h-7 w-24 py-0.5 text-sm"
              >
                {DAY_NAMES.map((d, n) => (
                  <option key={d} value={n}>
                    {d}
                  </option>
                ))}
              </Select>
              <Input
                type="time"
                aria-label={`Slot ${i + 1} time`}
                value={row.time}
                disabled={disabled}
                onChange={(e) => update(i, { time: e.target.value })}
                className="min-h-7 w-28 py-0.5 font-mono text-sm"
              />
              <Button
                size="icon-sm"
                variant="ghost"
                disabled={disabled}
                onClick={() => remove(i)}
                aria-label={`Remove ${DAY_NAMES[row.day] ?? ""} ${row.time}`}
              >
                <Icon name="x" size="sm" />
              </Button>
            </li>
          ))}
        </ul>
      )}
      {disabled ? null : (
        <div>
          <Button size="sm" variant="ghost" onClick={add}>
            <Icon name="plus" size="sm" />
            Add slot
          </Button>
        </div>
      )}
    </div>
  );
}

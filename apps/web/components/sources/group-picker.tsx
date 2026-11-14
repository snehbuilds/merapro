"use client";

import { useId } from "react";
import { GroupDot } from "@/components/mera/group-dot";
import { Chip } from "@/components/ui/chip";
import { Hint } from "@/components/ui/field";
import { groupSwatch } from "@/lib/groups";

/*
 * sources.html "Feeds which groups": one toggle chip per group. None pressed
 * means the source feeds the whole workspace.
 */
export interface PickerGroup {
  id: string;
  name: string;
  color: string | null;
}

export function GroupPicker({
  groups,
  value,
  onChange,
  disabled,
  error,
}: {
  groups: readonly PickerGroup[];
  value: readonly string[];
  onChange: (next: string[]) => void;
  disabled?: boolean;
  error?: string;
}) {
  const labelId = useId();
  const hintId = useId();
  const errorId = useId();
  const toggle = (id: string) => onChange(value.includes(id) ? value.filter((v) => v !== id) : [...value, id]);
  return (
    <div className="grid gap-1.5">
      {/* Names the chip group (a <label> can only name one form control). */}
      <span id={labelId} className="text-sm font-semibold text-fg">
        Feeds which groups
      </span>
      {groups.length === 0 ? (
        <Hint>This workspace has no groups yet, so the source feeds the whole workspace.</Hint>
      ) : (
        <>
          <div
            role="group"
            aria-labelledby={labelId}
            aria-describedby={[hintId, error ? errorId : null].filter(Boolean).join(" ")}
            className="flex flex-wrap gap-1.5"
          >
            {groups.map((g) => (
              <Chip key={g.id} pressed={value.includes(g.id)} disabled={disabled} onClick={() => toggle(g.id)}>
                <GroupDot group={g.id} style={{ background: groupSwatch(g) }} />
                {g.name}
              </Chip>
            ))}
          </div>
          <Hint id={hintId}>
            {value.length === 0 ? "None selected: feeds the whole workspace." : "Leave all unselected to feed the whole workspace."}
          </Hint>
        </>
      )}
      {error ? (
        <p id={errorId} role="alert" className="text-caption font-medium text-danger">
          {error}
        </p>
      ) : null}
    </div>
  );
}

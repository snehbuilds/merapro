import Link from "next/link";
import { Notice } from "@/components/ui/notice";

export function WorkspaceAccessNotice({ slug }: { slug: string }) {
  return (
    <Notice tone="warn">
      Admin and Owner tools require two-step sign-in.{" "}
      <Link className="underline" href={`/w/${slug}/security`}>Open your security settings</Link> to enable it.
    </Notice>
  );
}

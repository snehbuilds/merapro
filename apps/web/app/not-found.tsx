import Link from "next/link";
import { Icon } from "@/components/icons";
import { Button } from "@/components/ui/button";

export default function NotFound() {
  return (
    <main id="main" className="grid min-h-screen place-items-center p-6">
      <div className="grid justify-items-center gap-3 text-center">
        <Icon name="discovery" size="xl" className="text-fg-subtle" />
        <h1>Nothing here</h1>
        <p className="max-w-[46ch] text-sm text-fg-muted">
          This screen or workspace does not exist, you are not a member of it, or it is not switched on yet.
        </p>
        <Button asChild variant="primary">
          <Link href="/">Go to your workspace</Link>
        </Button>
      </div>
    </main>
  );
}

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { readSessionTokens } from "@/lib/session/cookies";
import { serverClient } from "@/lib/api/server";
import { me, listWorkspaces } from "@mera/sdk";
import { screenHref } from "@/lib/nav";
import { LandingView } from "@/components/landing";

async function getAuthSession() {
  try {
    const tokens = readSessionTokens(await cookies());
    if (!tokens.accessToken) return null;
    const client = await serverClient();
    const [userRes, pageRes] = await Promise.all([
      me({ client }),
      listWorkspaces({ client }),
    ]);
    if (!userRes.response?.ok || !userRes.data) return null;
    return {
      user: userRes.data,
      workspaces: pageRes.data?.data || [],
    };
  } catch {
    return null;
  }
}

export default async function Home() {
  const session = await getAuthSession();
  if (session) {
    const first = session.workspaces[0];
    redirect(first ? screenHref(first.slug, "dashboard") : "/welcome");
  }

  return <LandingView />;
}

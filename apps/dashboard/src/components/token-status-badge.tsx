import { Badge } from "@/components/ui/badge";
import type { TokenStatus } from "@/api/types";
import { TOKEN_STATUS_LABEL, TOKEN_STATUS_VARIANT } from "@/lib/token-display";

/**
 * Issue #368: the one renderer for `TokenOut.status`, shared by the Connect
 * SDK flow and the admin token list so the two lists cannot disagree about
 * what "active" looks like. The status is always rendered as TEXT as well as
 * colour.
 */
export function TokenStatusBadge({
  status,
  "data-testid": testId,
}: {
  status: TokenStatus;
  "data-testid"?: string;
}) {
  const label = TOKEN_STATUS_LABEL[status];
  // A status the client does not know (a newer Control Server) is shown as
  // unknown rather than guessed — never re-derived from revoked/expires_at.
  if (label === undefined) {
    return (
      <Badge variant="outline" data-testid={testId}>
        不明
      </Badge>
    );
  }
  return (
    <Badge variant={TOKEN_STATUS_VARIANT[status]} data-testid={testId}>
      {label}
    </Badge>
  );
}

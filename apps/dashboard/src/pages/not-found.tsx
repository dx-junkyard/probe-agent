import { Link, useLocation, useNavigate } from "react-router-dom";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

// Issue #466 (UX-18): 古い URL・タイプミスを空白画面にしない。AppLayout の中に
// 描くので、ヘッダー・ナビゲーションは残り、原因と復帰方法が分かる。
export default function NotFoundPage() {
  const location = useLocation();
  const navigate = useNavigate();
  // 直接開いたリンクでは戻る先が無い (history の先頭)。
  const canGoBack = typeof window !== "undefined" && (window.history.state?.idx ?? 0) > 0;
  return (
    <Card className="max-w-xl" data-testid="not-found">
      <CardHeader>
        <CardTitle as="h1" className="text-xl">ページが見つかりません</CardTitle>
        <CardDescription>
          開こうとした URL に対応する画面がありません。URL の入力間違いか、名前が変わった古いリンクの可能性があります。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm">
          開いた URL: <code className="rounded bg-muted px-1.5 py-0.5 text-xs break-all" data-testid="not-found-path">
            {location.pathname}{location.search}{location.hash}
          </code>
        </p>
        <div className="flex flex-wrap gap-2">
          <Link to="/" className={buttonVariants()} data-testid="not-found-overview">Overview へ移動</Link>
          {canGoBack && (
            <Button variant="outline" onClick={() => navigate(-1)} data-testid="not-found-back">
              前の画面に戻る
            </Button>
          )}
        </div>
        <p className="text-xs text-muted-foreground">
          目的の画面は左のナビゲーション(狭い画面ではメニューボタン)からも探せます。
        </p>
      </CardContent>
    </Card>
  );
}

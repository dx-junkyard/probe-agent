// 利用中のセッション失効 (401) を client から AuthProvider へ伝えるイベント名。
// client と auth の両方が参照するので、どちらのモックにも依存しない場所に置く。
export const UNAUTHORIZED_EVENT = "probe-agent:unauthorized";

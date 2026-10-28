const kes = new Intl.NumberFormat("en-KE", { maximumFractionDigits: 2, minimumFractionDigits: 0 });

export function money(amount: number, currency = "KES"): string {
  const sign = amount < 0 ? "−" : "";
  return `${sign}${currency} ${kes.format(Math.abs(amount))}`;
}

export function shortDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-KE", { day: "numeric", month: "short" });
}

export function time(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-KE", { hour: "2-digit", minute: "2-digit" });
}

export function when(iso: string): string {
  return new Date(iso).toLocaleString("en-KE", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

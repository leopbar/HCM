import type { Metadata } from "next";
import { Bebas_Neue, Open_Sans } from "next/font/google";
import "./globals.css";
import "./vacation.css";
import "./hr-modules.css";
import "./environment.css";
import "./theme.css";

// Self-hosted at build time: the browser never calls Google Fonts.
const openSans = Open_Sans({ subsets: ["latin"], weight: ["400", "500", "600", "700"], variable: "--font-open-sans", display: "swap" });
const bebas = Bebas_Neue({ subsets: ["latin"], weight: "400", variable: "--font-bebas", display: "swap" });

export const metadata: Metadata = {
  title: "HCM Lab — Simulador de Gestão de Pessoas",
  description: "Laboratório local para aprender processos e parametrizações de RH.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="pt-BR" className={`${openSans.variable} ${bebas.variable}`}><body>{children}</body></html>;
}

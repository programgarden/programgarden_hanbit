import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { Providers } from "@/lib/query/provider";
import { TopBar } from "@/components/TopBar";
import { SideNav } from "@/components/SideNav";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "HANBIT — Trading Dashboard",
  description: "programgarden_hanbit 자동화매매 대시보드",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="ko"
      className={`${geistSans.variable} ${geistMono.variable} h-full`}
    >
      <body className="flex h-full flex-col bg-background text-foreground">
        {/* App Shell: top global bar + left nav + main content.
            Providers (React Query + WS stream) is the one client boundary; the
            server-rendered shell below is passed through as children. */}
        <Providers>
          <TopBar />
          <div className="flex min-h-0 flex-1">
            <SideNav />
            <main className="min-w-0 flex-1 overflow-auto p-4">{children}</main>
          </div>
        </Providers>
      </body>
    </html>
  );
}

import type { Metadata } from "next";

import "./globals.css";
import { ConsoleProvider } from "@/components/console/console-provider";
import { AppShell } from "@/components/shell/app-shell";
import { ThemeProvider } from "@/components/theme-provider";

export const metadata: Metadata = {
  title: {
    default: "Telephony Voice Simulator",
    template: "%s · Telephony Voice Simulator",
  },
  description:
    "One simulator for answering machines, IVRs, PBX routing, and voice-agent call testing.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="font-sans antialiased">
        <ThemeProvider
          attribute="class"
          defaultTheme="system"
          enableSystem
          disableTransitionOnChange
        >
          <ConsoleProvider>
            <AppShell>{children}</AppShell>
          </ConsoleProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}

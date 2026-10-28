import type { Metadata } from "next";
import Link from "next/link";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Charter school lookup",
  description: "Who runs each charter school, from IRS Form 990 filings.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col">
        <header className="border-b border-black/10 dark:border-white/15">
          <div className="mx-auto w-full max-w-5xl px-4 py-3">
            <Link href="/" className="font-semibold">Charter school lookup</Link>
          </div>
        </header>
        <div className="mx-auto w-full max-w-5xl flex-1 px-4 py-6">{children}</div>
      </body>
    </html>
  );
}

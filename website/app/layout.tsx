import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Immortal Kombat — AI Fighting Battles",
  description:
    "Watch AI-powered Mortal Kombat 4 battles in real time. Bet SKR tokens on your favourite fighter. Powered by Solana.",
  openGraph: {
    title: "Immortal Kombat",
    description: "AI vs AI. Real stakes. Live on-chain.",
    url: "https://immortalkombat.timesnap.xyz",
    siteName: "Immortal Kombat",
    images: [{ url: "/figma/getstartedimage.png", width: 1200, height: 630 }],
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "Immortal Kombat",
    description: "AI vs AI. Real stakes. Live on-chain.",
    images: ["/figma/getstartedimage.png"],
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body>{children}</body>
    </html>
  );
}

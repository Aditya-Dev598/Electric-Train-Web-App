import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'UK Rail Timetable Generator',
  description: 'Generate railway timetable and route CSV outputs from official UK rail data sources',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>{children}</body>
    </html>
  );
}

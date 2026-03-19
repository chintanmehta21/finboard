import { Montserrat } from 'next/font/google';
import { Analytics } from '@vercel/analytics/next';
import './globals.css';

const montserrat = Montserrat({
  subsets: ['latin'],
  weight: ['700'],
  variable: '--font-montserrat',
});

export const metadata = {
  title: 'FinBoard — Market Signals',
  description: 'Daily quantitative signal dashboard for NSE 500 stocks',
  icons: { icon: '/favicon.svg' },
  openGraph: {
    title: 'FinBoard — Market Signals',
    description: 'Daily quantitative signal dashboard for NSE 500 stocks',
    type: 'website',
  },
};

export default function RootLayout({ children }) {
  return (
    <html lang="en" className={montserrat.variable}>
      <body>
        {children}
        <Analytics />
      </body>
    </html>
  );
}

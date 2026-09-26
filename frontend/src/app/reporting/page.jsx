import { redirect } from 'next/navigation';

// The reports hub/grid was removed -- ReportTabs on each report page already
// covers switching between them, so this route just forwards to the first
// tab instead of showing a redundant intermediate screen.
export default function ReportsIndexRedirect() {
  redirect('/reporting/sales');
}

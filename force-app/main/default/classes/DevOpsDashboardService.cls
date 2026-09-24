public with sharing class DevOpsDashboardService {

    public static Map<String, Object> getOrgHealthMetrics() {
        Map<String, Object> metrics = new Map<String, Object>();
        
        // Active user count
        metrics.put('activeUsers', [SELECT COUNT() FROM User WHERE IsActive = true]);
        
        // Apex class count
        metrics.put('apexClasses', [SELECT COUNT() FROM ApexClass WHERE Status = 'Active']);
        
        // Scheduled job count
        metrics.put('scheduledJobs', [SELECT COUNT() FROM CronTrigger WHERE State = 'WAITING']);
        
        // Recent async job failures
        metrics.put('asyncFailures', [SELECT COUNT() FROM AsyncApexJob 
                                       WHERE Status = 'Failed' 
                                       AND CreatedDate = LAST_N_DAYS:7]);
        
        return metrics;
    }

    public static List<AsyncApexJob> getRecentJobs(Integer days) {
        return [SELECT Id, ApexClass.Name, Status, JobType, 
                       CreatedDate, CompletedDate, NumberOfErrors
                FROM AsyncApexJob 
                WHERE CreatedDate = LAST_N_DAYS:7
                ORDER BY CreatedDate DESC 
                LIMIT 20];
    }
}

@isTest
public class DevOpsDashboardServiceTest {

    @isTest
    static void testGetOrgHealthMetrics() {
        Test.startTest();
        Map<String, Object> metrics = DevOpsDashboardService.getOrgHealthMetrics();
        Test.stopTest();
        System.assertNotEquals(null, metrics, 'Metrics map should not be null');
        System.assert(metrics.containsKey('activeUsers'), 'Should contain activeUsers key');
        System.assert(metrics.containsKey('apexClasses'), 'Should contain apexClasses key');
        System.assert(metrics.containsKey('scheduledJobs'), 'Should contain scheduledJobs key');
    }

    @isTest
    static void testGetRecentJobs() {
        Test.startTest();
        List<AsyncApexJob> jobs = DevOpsDashboardService.getRecentJobs(7);
        Test.stopTest();
        System.assertNotEquals(null, jobs, 'Jobs list should not be null');
    }
}

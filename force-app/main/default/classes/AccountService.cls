public with sharing class AccountService {
    public static List<Account> getActiveAccounts() {
        return [SELECT Id, Name, Industry, AnnualRevenue 
                FROM Account 
                WHERE IsActive__c = true 
                ORDER BY Name];
    }

    public static void updateAccountStatus(List<Id> accountIds, String status) {
        List<Account> accs = [SELECT Id, IsActive__c FROM Account WHERE Id IN :accountIds];
        for (Account a : accs) {
            a.IsActive__c = (status == 'Active');
        }
        update accs;
    }
}

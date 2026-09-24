@isTest
public class AccountServiceTest {
    @testSetup
    static void setup() {
        List<Account> accounts = new List<Account>();
        for (Integer i = 0; i < 5; i++) {
            accounts.add(new Account(Name = 'Test Account ' + i));
        }
        insert accounts;
    }

    @isTest
    static void testGetActiveAccounts() {
        Test.startTest();
        List<Account> result = AccountService.getActiveAccounts();
        Test.stopTest();
        System.assertNotEquals(null, result, 'Result should not be null');
    }
}
